"""JSON de métadonnées : file text-jobs, attente dans work/json-metadata/, rattachement au document."""

import json

import pytest

from fakes import FakeBlobService, MemoryStore, event, run_chain
from pseudo.config import Settings
from pseudo.handler import handle_pdf_job
from pseudo.jobs import Job, document_id
from pseudo.json_jobs import attach_json_metadata, handle_json_job
from pseudo.models import Entity
from pseudo.router import QUEUE_TEXT, route_event
from pseudo.runtime import Runtime
from pseudo.text_jobs import handle_text_job
from test_classification import FakeClassifier, scores
from test_json_metadata import CATALOG, GED, NAMES
from test_pipeline import FakeDetector, make_text_pdf, services

DOC = "lot1/Acte de deces - 9000001.pdf"
JSON_NAME = DOC + ".json"


@pytest.fixture
def settings(monkeypatch):
    """JSON activés, valeurs fictives."""
    monkeypatch.setenv("PSEUDO_OUTPUT_MODE", "substitution")
    monkeypatch.setenv("JSON_METADATA_ENABLED", "true")
    return Settings.from_env()


def runtime(settings, with_document=True, detector=None, classifier=None):
    """Runtime de test : le document (facultatif) et son JSON sont dans incoming/."""
    blobs = FakeBlobService()
    if with_document:
        blobs.put("incoming", DOC, make_text_pdf())
    blobs.put("incoming", JSON_NAME, json.dumps(GED).encode())
    svc = services(
        detector=detector or FakeDetector({**NAMES, "DUPONT": "NOM", "Jean": "PRENOM"}, only_first=False),
        classifier=classifier or FakeClassifier(scores(("DENTAIRE", 0.9))),
        fields=lambda: CATALOG,
    )
    return Runtime(settings=settings, services=svc, mapping_store=MemoryStore(), blob_service=blobs)


class UnlocatedOnPdf(FakeDetector):
    """Signale sur le PDF une valeur introuvable : le document part en quarantaine
    (entites_non_localisees) ; le JSON, lui, est détecté normalement."""

    def detect(self, lines):
        """Entité introuvable si la ligne « Patient » du PDF de test est présente."""
        if any("Patient" in line.text for line in lines):
            return [Entity(lines[0].id, "INTROUVABLE", "NOM")]
        return super().detect(lines)


def run_json(rt) -> str:
    """Routeur puis tâche text-jobs pour le JSON ; renvoie le statut de la tâche."""
    route = route_event(event(JSON_NAME), rt)
    assert route.queue == QUEUE_TEXT
    return handle_text_job(route.job.to_json(), rt)


def test_router_sends_json_to_text_jobs_with_the_document_id(settings, monkeypatch):
    rt = runtime(settings)
    route = route_event(event(JSON_NAME), rt)
    assert route.queue == QUEUE_TEXT and route.job.origin == "json-metadata"
    assert route.job.document_id == document_id(f"https://st.blob.core.windows.net/incoming/{DOC}")
    monkeypatch.setenv("JSON_METADATA_ENABLED", "false")
    rt_off = runtime(Settings.from_env())
    assert route_event(event(JSON_NAME), rt_off).status == "ignored"  # ignoré, pas de quarantaine
    assert rt_off.blob_service.names("quarantine") == []


def test_json_after_the_document_is_published_next_to_it(settings):
    rt = runtime(settings)
    assert run_chain(rt, DOC)[0] == "ok"
    assert run_json(rt) == "ok"
    published = rt.blob_service.names("pseudonymised")
    assert f"dentaire/{JSON_NAME}" in published and f"dentaire/{DOC}" in published
    data = json.loads(rt.blob_service.data("pseudonymised", f"dentaire/{JSON_NAME}"))
    assert data["DOCUMENT"] == GED["DOCUMENT"] and "DUPONT" not in json.dumps(data, ensure_ascii=False)
    meta = rt.blob_service.blobs[("pseudonymised", f"dentaire/{JSON_NAME}")]["metadata"]
    assert meta["document_type"] == "dentaire"  # mêmes métadonnées de classement que le document
    # Le chemin du JSON est ajouté à la trace du document (nettoyage lors d'un retraitement).
    assert f"pseudonymised/dentaire/{JSON_NAME}" in rt.mapping_store.traces[-1]["sorties"]
    # Ré-identification : occurrences repérées par le chemin du champ, sous <document>-json.
    occ = [o for o in rt.mapping_store.occurrences if o["documentId"].endswith("-json")]
    assert occ and all(o["chemin"].startswith("GED_metadata.") for o in occ)
    # Le JSON reste dans work/ : un document retraité le recopiera.
    assert any(n.startswith("json-metadata/") for n in rt.blob_service.names("work"))


def test_json_before_the_document_waits_in_work_then_is_attached(settings):
    rt = runtime(settings)
    assert run_json(rt) == "en_attente"
    assert rt.blob_service.names("pseudonymised") == []
    doc_id = document_id(f"https://st.blob.core.windows.net/incoming/{DOC}")
    assert f"json-metadata/{doc_id}.json" in rt.blob_service.names("work")
    # Le document est traité ensuite : une fois sa trace écrite, il rattache lui-même le JSON.
    assert run_chain(rt, DOC)[0] == "ok"
    assert f"dentaire/{JSON_NAME}" in rt.blob_service.names("pseudonymised")
    assert f"pseudonymised/dentaire/{JSON_NAME}" in rt.mapping_store.traces[-1]["sorties"]
    # Un second rattachement (JSON relivré, document retraité…) réécrit le même fichier, sans erreur.
    assert attach_json_metadata(rt, doc_id, "incoming", DOC) == "ok"


def test_document_in_quarantine_gets_the_original_json(settings):
    rt = runtime(settings, detector=UnlocatedOnPdf(NAMES, only_first=False))
    assert run_chain(rt, DOC)[0] == "quarantine"
    assert run_json(rt) == "quarantine"
    quarantined = rt.blob_service.names("quarantine")
    assert f"dentaire/{JSON_NAME}" in quarantined
    assert json.loads(rt.blob_service.data("quarantine", f"dentaire/{JSON_NAME}")) == GED  # original
    assert rt.blob_service.names("pseudonymised") == []


def test_unreadable_json_goes_to_quarantine_next_to_the_published_document(settings):
    rt = runtime(settings)
    rt.blob_service.put("incoming", JSON_NAME, b"{pas du json")
    assert run_chain(rt, DOC)[0] == "ok"
    assert run_json(rt) == "quarantine"
    quarantined = rt.blob_service.names("quarantine")
    assert f"dentaire/{JSON_NAME}" in quarantined and f"dentaire/{DOC}.json.motif.json" in quarantined
    motif = json.loads(rt.blob_service.data("quarantine", f"dentaire/{DOC}.json.motif.json"))
    assert motif["motifs"] == ["json_illisible"]
    assert f"dentaire/{JSON_NAME}" not in rt.blob_service.names("pseudonymised")


def test_json_of_a_mail_goes_into_the_mail_folder(settings, monkeypatch):
    from test_mails import make_mail

    monkeypatch.setenv("MAIL_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "true")
    rt = runtime(Settings.from_env(), with_document=False)
    name = "lot1/Acte de deces + Correspondance - 9000001.eml"
    rt.blob_service.put("incoming", name, make_mail())
    rt.blob_service.put("incoming", name + ".json", json.dumps(GED).encode())
    assert run_chain(rt, name)[0] == "ok"
    route = route_event(event(name + ".json"), rt)
    assert handle_text_job(route.job.to_json(), rt) == "ok"
    folder = {n.rsplit("/", 1)[0] for n in rt.blob_service.names("pseudonymised") if n.endswith("/mail.json")}.pop()
    assert f"{folder}/Acte de deces + Correspondance - 9000001.eml.json" in rt.blob_service.names("pseudonymised")


def test_json_before_a_photo_is_attached_when_the_photo_is_published(settings, monkeypatch):
    from pseudo.geometry import load_geometry
    from pseudo.images.prepare import handle_image_job
    from test_images import PhotoOcr, make_photo

    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "true")
    rt = runtime(Settings.from_env(), with_document=False)
    photo, p = make_photo()
    rt.blob_service.put("incoming", "lot1/photo.jpg", photo)
    rt.blob_service.put("incoming", "lot1/photo.jpg.json", json.dumps(GED).encode())
    route = route_event(event("lot1/photo.jpg.json"), rt)
    assert handle_text_job(route.job.to_json(), rt) == "en_attente"

    def prepare(job_json, ctx):
        job = handle_image_job(job_json, ctx)
        rt.services.ocr = PhotoOcr(p, load_geometry(ctx.blob_service.data("work", job.prepared.transforms)))
        return job

    assert run_chain(rt, "lot1/photo.jpg", prepare_image=prepare)[0] == "ok"
    assert "dentaire/lot1/photo.jpg.json" in rt.blob_service.names("pseudonymised")


def test_json_before_a_file_refused_by_the_router_follows_it_into_quarantine(settings):
    rt = runtime(settings, with_document=False)
    rt.blob_service.put("incoming", "lot1/contrat.docx", b"PK...")
    rt.blob_service.put("incoming", "lot1/contrat.docx.json", json.dumps(GED).encode())
    route = route_event(event("lot1/contrat.docx.json"), rt)
    assert handle_text_job(route.job.to_json(), rt) == "en_attente"
    assert route_event(event("lot1/contrat.docx"), rt).status == "quarantine"  # format non pris en charge
    quarantined = rt.blob_service.names("quarantine")
    assert "non-classe/lot1/contrat.docx" in quarantined
    assert "non-classe/lot1/contrat.docx.json" in quarantined  # JSON d'origine, à côté
    assert json.loads(rt.blob_service.data("quarantine", "non-classe/lot1/contrat.docx.json")) == GED


def test_redelivered_document_job_attaches_a_waiting_json(settings):
    rt = runtime(settings)
    status, messages = run_chain(rt, DOC)
    assert status == "ok"
    # Le JSON s'écrit dans work/ pendant que la trace du document n'est pas encore lisible (fenêtre
    # de concurrence simulée) : il attend.
    last = rt.mapping_store.traces[-1]
    rt.mapping_store.traces.append({**last, "status": "en_cours"})
    assert handle_json_job(Job.from_json(route_event(event(JSON_NAME), rt).job.to_json()), rt) == "en_attente"
    rt.mapping_store.traces.pop()
    # Le message du document est relivré : il ne retraite pas le document, mais rattache le JSON.
    assert handle_pdf_job(messages[-1], rt) == "already_processed"
    assert f"dentaire/{JSON_NAME}" in rt.blob_service.names("pseudonymised")


def test_no_attachment_when_json_metadata_are_disabled(settings, monkeypatch):
    rt = runtime(settings)
    assert run_json(rt) == "en_attente"  # JSON prêt dans work/ pendant que les JSON sont activés
    monkeypatch.setenv("JSON_METADATA_ENABLED", "false")
    rt.settings = Settings.from_env()
    assert run_chain(rt, DOC)[0] == "ok"
    assert f"dentaire/{JSON_NAME}" not in rt.blob_service.names("pseudonymised")  # désactivé : rien de copié
