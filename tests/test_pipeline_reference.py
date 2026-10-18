"""Résultats de référence de run_pipeline : filet de sécurité de la refonte du pipeline en étapes.

Chaque cas exécute le pipeline et compare TOUT ce qu'il rend (statut, motifs, rapport complet,
textes, zones, occurrences, correspondances, classification, texte du document produit) à la
référence enregistrée dans tests/data/pipeline_reference.json. Une refonte qui ne change pas le
comportement doit laisser cette comparaison intacte.

Seules les valeurs qui varient d'une exécution à l'autre sont neutralisées : durées et attentes,
valeur chiffrée des correspondances (nonce aléatoire), octets du PDF produit (on compare son texte).

Après un changement VOULU du comportement, régénérer la référence puis relire le diff :

    PIPELINE_REFERENCE_UPDATE=1 uv run --python 3.12 --with-requirements requirements-dev.txt pytest tests/test_pipeline_reference.py
"""

import dataclasses
import json
import os
from pathlib import Path

import pymupdf
import pytest

from pseudo.config import Settings
from pseudo.json_metadata import build_json_text
from pseudo.json_rules import JsonRules
from pseudo.pipeline import PART, PipelineInput, run_pipeline
from test_classification import FakeClassifier, scores
from test_handwriting import HandwrittenOcr
from test_json_metadata import GED
from test_pipeline import DICTIONARY, FakeDetector, FakeVerifier, TruthOcr, make_scanned_pdf, make_text_pdf, services

REFERENCE = Path(__file__).resolve().parent / "data" / "pipeline_reference.json"
UPDATE = os.environ.get("PIPELINE_REFERENCE_UPDATE") == "1"
VOLATILE = ("duree", "attente")  # clés dont la valeur dépend de la machine


class StatsDetector(FakeDetector):
    """Détecteur simulé qui expose sa consommation, comme l'agent réel."""

    last_stats = {"appels": 1, "entites_rendues": 3, "jetons_entree": 1200, "jetons_sortie": 150, "jetons_raisonnement": 64}


class StatsLanguage:
    """Azure AI Language simulé : aucune détection, volume envoyé exposé."""

    last_stats = {"enregistrements": 1, "caracteres": 120}

    def detect(self, lines):
        """Rien à signaler."""
        return []


def _settings(monkeypatch, **env) -> Settings:
    """Réglages par défaut du pipeline, plus ceux du cas."""
    for key in ("FORCE_OCR", "VERIFICATION_ROUNDS", "OCR_NATIVE_PAGES", "HANDWRITING_POLICY", "REPORT_DETAIL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings.from_env()


def _full_services(**kwargs):
    """Services de test complets : détection avec consommation, vérificateur qui trouve un résidu,
    Language, classement."""
    defaults = dict(
        detector=StatsDetector(DICTIONARY),
        verifier=FakeVerifier("Lilas"),
        language=StatsLanguage(),
        classifier=FakeClassifier(scores(("DENTAIRE", 0.9), ("OPTIQUE", 0.4))),
    )
    defaults.update(kwargs)
    return services(**defaults)


def case_native_tokens(monkeypatch):
    """PDF natif, sortie en jetons, rapport diagnostic, deux tours de vérification."""
    return run_pipeline(make_text_pdf(), _settings(monkeypatch), _full_services())


def case_native_substitution(monkeypatch):
    """PDF natif, valeurs fictives, rapport standard."""
    settings = _settings(monkeypatch, PSEUDO_OUTPUT_MODE="substitution", REPORT_DETAIL="standard")
    return run_pipeline(make_text_pdf(), settings, _full_services())


def case_part(monkeypatch):
    """Pièce jointe d'un mail : PDF natif analysé et rendu, sans classement (le mail l'est en entier)."""
    return run_pipeline(make_text_pdf(), _settings(monkeypatch), _full_services(), steps=PART)


def case_scanned(monkeypatch):
    """PDF scanné lu par l'OCR, sans vérificateur ni Language."""
    pdf, truth = make_scanned_pdf()
    svc = services(detector=StatsDetector(DICTIONARY), ocr=TruthOcr(truth), verifier=None)
    return run_pipeline(pdf, _settings(monkeypatch), svc)


def case_handwritten(monkeypatch):
    """Écriture manuscrite détectée : quarantaine après l'OCR, avant l'IA."""
    pdf, truth = make_scanned_pdf()
    svc = services(ocr=HandwrittenOcr(truth))
    return run_pipeline(pdf, _settings(monkeypatch, HANDWRITING_POLICY="quarantaine"), svc)


def case_unreadable(monkeypatch):
    """Contenu qui n'est pas un PDF : rejet à l'ouverture."""
    return run_pipeline(b"ceci n'est pas un PDF", _settings(monkeypatch), _full_services())


def case_text_content(monkeypatch):
    """Texte déjà extrait (JSON de métadonnées), sans classement ni rendu."""
    built = build_json_text(GED, JsonRules())
    detector = StatsDetector({"DUPONT": "NOM", "Jean": "PRENOM", "MARTIN": "NOM", "Claire": "PRENOM"}, only_first=False)
    source = PipelineInput(pdf=b"", content=built.content, source={"origine": "json-metadata", "format": "json"})
    settings = _settings(monkeypatch, PSEUDO_OUTPUT_MODE="substitution")
    return run_pipeline(source, settings, _full_services(detector=detector))


CASES = {
    name.removeprefix("case_"): fn for name, fn in sorted(globals().items()) if name.startswith("case_") and callable(fn)
}


def _neutral(value, key: str = "", volatile: bool = False):
    """Copie JSON de la valeur, valeurs volatiles neutralisées (une clé « duree… » ou « attente… »
    et tout ce qu'elle contient), nombres décimaux arrondis."""
    volatile = volatile or any(word in key for word in VOLATILE)
    if isinstance(value, dict):
        return {k: _neutral(v, k, volatile) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_neutral(v, key, volatile) for v in value]
    if volatile and isinstance(value, (int, float)) and not isinstance(value, bool):
        return "<variable>"
    if isinstance(value, float):
        return round(value, 4)
    return value


def snapshot(result) -> dict:
    """Tout ce que rend le pipeline, sous une forme comparable d'une exécution à l'autre."""
    output_text = None
    if result.output:
        output_text = "".join(page.get_text() for page in pymupdf.open(stream=result.output, filetype="pdf"))
    return _neutral({
        "status": result.status,
        "reasons": result.reasons,
        "report": result.report,
        "text": result.text,
        "token_text": result.token_text,
        "spans": [dataclasses.asdict(s) for s in result.spans],
        "occurrences": result.occurrences,
        "records": [{"id": r.id, "type": r.type, "fingerprint": r.fingerprint} for r in result.records],
        "classification": result.classification.to_dict() if result.classification else None,
        "content_lines": [[ln.id, ln.text, ln.source] for ln in result.content.lines()] if result.content else None,
        "output_text": output_text,
    })


@pytest.fixture(scope="module")
def reference() -> dict:
    """Résultats de référence enregistrés (vide en mode régénération)."""
    if UPDATE or not REFERENCE.exists():
        return {}
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(CASES))
def test_pipeline_matches_reference(name, monkeypatch, reference):
    current = snapshot(CASES[name](monkeypatch))
    if UPDATE:
        data = json.loads(REFERENCE.read_text(encoding="utf-8")) if REFERENCE.exists() else {}
        data[name] = current
        REFERENCE.parent.mkdir(parents=True, exist_ok=True)
        REFERENCE.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip("référence régénérée")
    assert name in reference, f"cas « {name} » absent de la référence : la régénérer (PIPELINE_REFERENCE_UPDATE=1)"
    assert current == reference[name]
