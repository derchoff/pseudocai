"""Classification du type de document et rangement des sorties par type (sans appel Azure)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from azure.core.exceptions import ResourceNotFoundError

from pseudo.classification import (
    STATUT_DESACTIVEE,
    STATUT_ERREUR,
    STATUT_NON_APPLICABLE,
    STATUT_NON_CLASSE,
    STATUT_OK,
    decide,
)
from pseudo.config import Settings
from pseudo.detection.llm import ClassificationAgent
from pseudo.doctypes import UNCLASSIFIED, DocTypeCatalogError, parse_doctypes
from pseudo.handler import handle_event
from pseudo.models import Entity
from pseudo.pipeline import run_pipeline
from pseudo.prompts import LocalPromptSource, PromptRepository
from pseudo.runtime import Runtime
from test_pipeline import make_text_pdf, services

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
CATALOG = PromptRepository(LocalPromptSource(PROMPTS)).doctypes()


@pytest.fixture
def settings(monkeypatch):
    for key in ("CLASSIFICATION_ENABLED", "CLASSIFICATION_MIN_CONFIDENCE", "UNCLASSIFIED_FOLDER", "OCR_NATIVE_PAGES"):
        monkeypatch.delenv(key, raising=False)
    return Settings.from_env()


def scores(*pairs):
    return [{"type": t, "confidence": c, "indices": [f"indice {t.lower()}"]} for t, c in pairs]


class FakeClassifier:
    def __init__(self, raw=None, error: Exception | None = None):
        self.raw = raw or []
        self.error = error
        self.prompt_sha256 = "sha-classif"
        self.texts: list[str] = []

    def classify(self, text):
        self.texts.append(text)
        if self.error:
            raise self.error
        return self.raw, CATALOG, self.prompt_sha256


# ---------------------------------------------------------------- référentiel des types


def test_referential_declares_the_four_types():
    assert CATALOG.types == ("DENTAIRE", "OPTIQUE", "KINESITHERAPIE", "PSYCHOLOGIE")
    assert CATALOG.folder("KINESITHERAPIE") == "kinesitherapie"
    assert "`NON_CLASSE`" in CATALOG.types_markdown()


def test_folder_defaults_to_lowercase_type():
    catalog = parse_doctypes("## MEDECIN_GENERALISTE — Médecine générale\n- **Indices** : x\n")
    assert catalog.folder("MEDECIN_GENERALISTE") == "medecin-generaliste"


@pytest.mark.parametrize(
    "markdown, message",
    [
        ("## NON_CLASSE — Réservé\n", "réservé"),
        ("## OPTIQUE — Optique\n- **Dossier** : Optique Lunettes\n", "invalide"),
        ("## A — a\n- **Dossier** : x\n## B — b\n- **Dossier** : x\n", "utilisé par"),
        ("## Dentaire — Soins\n", "MAJUSCULES"),
        ("# vide\n", "aucun type de document"),
    ],
)
def test_invalid_doctype_referential_is_rejected(markdown, message):
    with pytest.raises(DocTypeCatalogError, match=message):
        parse_doctypes(markdown)


def test_classification_prompt_is_rendered_without_placeholders():
    prompt = PromptRepository(LocalPromptSource(PROMPTS)).classification()
    assert "{{" not in prompt.text and "NOTE POUR LES MAINTENEURS" not in prompt.text
    assert "## DENTAIRE — Soins dentaires" in prompt.text
    assert prompt.doctypes.types == CATALOG.types


# ---------------------------------------------------------------- décision


def decide_(raw, **kw):
    params = dict(min_confidence=0.6, secondary_min_confidence=0.3, unclassified_folder="non-classe")
    params.update(kw)
    return decide(raw, CATALOG, **params)


def test_principal_and_secondary_types():
    c = decide_(scores(("OPTIQUE", 0.35), ("DENTAIRE", 0.92), ("PSYCHOLOGIE", 0.1)))
    assert (c.principal, c.dossier, c.statut, c.multi_type) == ("DENTAIRE", "dentaire", STATUT_OK, False)
    assert [(t.type, t.confidence) for t in c.types] == [("DENTAIRE", 0.92), ("OPTIQUE", 0.35)]


def test_mixed_document_sets_multi_type():
    c = decide_(scores(("DENTAIRE", 0.85), ("OPTIQUE", 0.8)))
    assert c.principal == "DENTAIRE" and c.multi_type is True
    assert [t.type for t in c.types] == ["DENTAIRE", "OPTIQUE"]


def test_low_confidence_goes_to_unclassified_but_keeps_scores():
    c = decide_(scores(("KINESITHERAPIE", 0.5)))
    assert (c.principal, c.dossier, c.statut) == (UNCLASSIFIED, "non-classe", STATUT_NON_CLASSE)
    assert [t.type for t in c.types] == ["KINESITHERAPIE"]


def test_agent_answering_non_classe_wins_over_weaker_type():
    c = decide_(scores((UNCLASSIFIED, 0.9), ("OPTIQUE", 0.65)))
    assert c.principal == UNCLASSIFIED and c.dossier == "non-classe"


def test_unknown_types_and_empty_answers_are_ignored():
    assert decide_(scores(("INCONNU", 0.99))).principal == UNCLASSIFIED
    assert decide_([]).principal == UNCLASSIFIED


def test_indices_are_truncated_and_metadata_is_ascii():
    raw = [{"type": "DENTAIRE", "confidence": 0.9, "indices": ["é" * 200] + [f"i{n}" for n in range(10)]}]
    c = decide_(raw)
    assert len(c.types[0].indices) == 5 and len(c.types[0].indices[0]) == 80
    meta = c.metadata()
    assert meta == {"document_type": "dentaire", "document_types": "DENTAIRE:0.90", "classification_statut": "ok"}
    assert all(v.isascii() for v in meta.values())


# ---------------------------------------------------------------- agent IA


class RecordingLlm:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def run(self, system_prompt, user_content, schema, schema_name="entities"):
        self.calls.append((system_prompt, user_content, schema, schema_name))
        return self.answer


def test_classification_agent_schema_and_truncation():
    llm = RecordingLlm({"types": scores(("DENTAIRE", 0.9))})
    repo = PromptRepository(LocalPromptSource(PROMPTS))
    agent = ClassificationAgent(llm, repo.classification, max_chars=50)
    raw, catalog, sha = agent.classify("x" * 500)
    _, user_content, schema, name = llm.calls[0]
    enum = schema["properties"]["types"]["items"]["properties"]["type"]["enum"]
    assert enum == [*CATALOG.types, UNCLASSIFIED]
    assert user_content == "<document>\n" + "x" * 50 + "\n</document>"
    assert name == "classification" and raw[0]["type"] == "DENTAIRE" and sha == agent.prompt_sha256


# ---------------------------------------------------------------- pipeline


def test_pipeline_classifies_the_pseudonymised_text(settings):
    classifier = FakeClassifier(scores(("DENTAIRE", 0.9), ("OPTIQUE", 0.4)))
    result = run_pipeline(make_text_pdf(), settings, services(classifier=classifier))
    assert result.status == "ok"
    assert result.classification.dossier == "dentaire"
    assert result.report["classification"]["principal"] == "DENTAIRE"
    assert result.report["classification"]["types"][1]["type"] == "OPTIQUE"
    # L'agent ne voit que le texte pseudonymisé : aucun nom en clair.
    assert "DUPONT" not in classifier.texts[0] and "[NOM_" in classifier.texts[0]


def test_quarantined_document_is_still_classified(settings):
    detector = services().detector
    detector.detect = lambda lines: [Entity("p1-l2", "INTROUVABLE", "NOM")]
    result = run_pipeline(
        make_text_pdf(), settings, services(detector=detector, classifier=FakeClassifier(scores(("OPTIQUE", 0.8))))
    )
    assert result.status == "quarantine" and result.classification.dossier == "optique"


def test_rejected_pdf_is_not_classified(settings):
    doc = pymupdf.open(stream=make_text_pdf(), filetype="pdf")
    encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    classifier = FakeClassifier(scores(("OPTIQUE", 0.8)))
    result = run_pipeline(encrypted, settings, services(classifier=classifier))
    assert result.classification.statut == STATUT_NON_APPLICABLE
    assert result.classification.dossier == "non-classe"
    assert result.report["classification"]["principal"] == UNCLASSIFIED
    assert classifier.texts == []


def test_classifier_failure_does_not_block_pseudonymisation(settings):
    result = run_pipeline(make_text_pdf(), settings, services(classifier=FakeClassifier(error=TimeoutError("429"))))
    assert result.status == "ok"
    assert (result.classification.statut, result.classification.dossier) == (STATUT_ERREUR, "non-classe")


def test_disabled_classification_writes_without_type_folder(settings, monkeypatch):
    monkeypatch.setenv("CLASSIFICATION_ENABLED", "false")
    result = run_pipeline(make_text_pdf(), Settings.from_env(), services(classifier=FakeClassifier()))
    assert (result.classification.statut, result.classification.dossier) == (STATUT_DESACTIVEE, "")


# ---------------------------------------------------------------- handler : rangement et retraitement


class FakeBlobService:
    """Stockage Blob en mémoire : {(container, nom): {"data": bytes, "metadata": dict}}."""

    def __init__(self):
        self.blobs: dict[tuple[str, str], dict] = {}

    def get_blob_client(self, container, name):
        blobs = self.blobs
        key = (container, name)

        def delete_blob():
            if key not in blobs:
                raise ResourceNotFoundError("absent")
            del blobs[key]

        return SimpleNamespace(
            exists=lambda: key in blobs,
            get_blob_properties=lambda: SimpleNamespace(size=len(blobs[key]["data"])),
            download_blob=lambda: SimpleNamespace(readall=lambda: blobs[key]["data"]),
            delete_blob=delete_blob,
        )

    def get_container_client(self, container):
        def upload_blob(name, data, overwrite=False, content_settings=None, metadata=None):
            self.blobs[(container, name)] = {"data": data, "metadata": metadata or {}}

        return SimpleNamespace(upload_blob=upload_blob)

    def names(self, container):
        return sorted(n for c, n in self.blobs if c == container)


class MemoryStore:
    def __init__(self):
        self.traces = []
        self.records = []

    def save(self, records):
        self.records += records

    def save_trace(self, trace):
        self.traces.append(trace)

    def last_trace(self, document_id):
        mine = [t for t in self.traces if t["documentId"] == document_id]
        return mine[-1] if mine else None


EVENT = json.dumps({"data": {"url": "https://st.blob.core.windows.net/incoming/lot1/facture.pdf"}})


def make_runtime(settings, svc, blobs=None, store=None):
    blobs = blobs or FakeBlobService()
    blobs.blobs[("incoming", "lot1/facture.pdf")] = {"data": make_text_pdf(), "metadata": {}}
    return Runtime(settings=settings, services=svc, mapping_store=store or MemoryStore(), blob_service=blobs)


def test_outputs_are_stored_in_the_type_folder(settings):
    runtime = make_runtime(settings, services(classifier=FakeClassifier(scores(("DENTAIRE", 0.92), ("OPTIQUE", 0.35)))))
    assert handle_event(EVENT, runtime) == "ok"
    blobs = runtime.blob_service
    assert blobs.names("pseudonymised") == [
        "dentaire/lot1/facture.pdf",
        "dentaire/lot1/facture.pseudonymise.txt",
        "dentaire/lot1/facture.rapport.json",
    ]
    meta = blobs.blobs[("pseudonymised", "dentaire/lot1/facture.pdf")]["metadata"]
    assert meta["document_type"] == "dentaire" and meta["document_types"] == "DENTAIRE:0.92;OPTIQUE:0.35"
    report = json.loads(blobs.blobs[("pseudonymised", "dentaire/lot1/facture.rapport.json")]["data"])
    assert report["classification"]["types"][0]["type"] == "DENTAIRE"
    trace = runtime.mapping_store.traces[-1]
    assert trace["classification"]["principal"] == "DENTAIRE"
    assert trace["sorties"][0] == "pseudonymised/dentaire/lot1/facture.pdf"


def test_quarantine_is_also_stored_by_type(settings):
    svc = services(classifier=FakeClassifier(scores(("PSYCHOLOGIE", 0.9))))
    svc.detector.detect = lambda lines: [Entity("p1-l2", "INTROUVABLE", "NOM")]
    runtime = make_runtime(settings, svc)
    assert handle_event(EVENT, runtime) == "quarantine"
    assert runtime.blob_service.names("quarantine") == ["psychologie/lot1/facture.pdf", "psychologie/lot1/facture.pdf.motif.json"]
    reason = json.loads(runtime.blob_service.blobs[("quarantine", "psychologie/lot1/facture.pdf.motif.json")]["data"])
    assert reason["classification"]["principal"] == "PSYCHOLOGIE" and "entites_non_localisees" in reason["motifs"]


def test_reprocessing_with_another_type_removes_previous_outputs(settings):
    blobs, store = FakeBlobService(), MemoryStore()
    handle_event(EVENT, make_runtime(settings, services(classifier=FakeClassifier(scores(("DENTAIRE", 0.9)))), blobs, store))
    handle_event(EVENT, make_runtime(settings, services(classifier=FakeClassifier(scores(("OPTIQUE", 0.9)))), blobs, store))
    assert all(n.startswith("optique/") for n in blobs.names("pseudonymised"))
    assert len(blobs.names("pseudonymised")) == 3


def test_reprocessing_into_quarantine_removes_previous_pseudonymised_outputs(settings):
    blobs, store = FakeBlobService(), MemoryStore()
    classifier = FakeClassifier(scores(("KINESITHERAPIE", 0.9)))
    handle_event(EVENT, make_runtime(settings, services(classifier=classifier), blobs, store))
    svc = services(classifier=classifier)
    svc.detector.detect = lambda lines: [Entity("p1-l2", "INTROUVABLE", "NOM")]
    handle_event(EVENT, make_runtime(settings, svc, blobs, store))
    assert blobs.names("pseudonymised") == []
    assert blobs.names("quarantine")[0] == "kinesitherapie/lot1/facture.pdf"
    assert ("incoming", "lot1/facture.pdf") in blobs.blobs  # l'entrée n'est jamais supprimée par le nettoyage
