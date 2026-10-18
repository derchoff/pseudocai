"""Fiches de statistiques (container « statistiques ») et consommation relevée par traitement."""

import json
from email import message_from_bytes, policy

import pytest

from fakes import FakeBlobService, MemoryStore, event, run_chain
from pseudo.config import Settings
from pseudo.handler import handle_pdf_job
from pseudo.pipeline import run_pipeline
from pseudo.router import route_event
from pseudo.runtime import Runtime
from pseudo.statistics import build_items
from pseudo.statistics.usage import empty_consumption, language_records, merge
from test_classification import FakeClassifier, make_runtime, scores
from test_mails import _gif, _png, make_mail, runtime as mail_runtime
from test_pipeline import DICTIONARY, FakeDetector, make_text_pdf, services


@pytest.fixture
def settings(monkeypatch):
    """Mails et images activés, fuseau par défaut (Europe/Paris)."""
    monkeypatch.setenv("MAIL_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("IMAGE_PROCESSING_ENABLED", "true")
    monkeypatch.setenv("JSON_METADATA_ENABLED", "true")
    monkeypatch.delenv("DEPOSIT_TIMEZONE", raising=False)
    return Settings.from_env()


class CountingDetector(FakeDetector):
    """Détecteur simulé qui expose sa consommation, comme l'agent réel (last_stats)."""

    last_stats = {"appels": 2, "jetons_entree": 1500, "jetons_sortie": 300, "jetons_raisonnement": 120}


class CountingVerifier:
    """Vérificateur simulé : rien à signaler, 1 appel de 800 jetons par tour."""

    prompt_sha256 = "fake"
    last_stats = {"appels": 1, "jetons_entree": 800, "jetons_sortie": 20, "jetons_raisonnement": 10}

    def verify(self, lines):
        """Aucun résidu."""
        return []


def items(rt) -> list[dict]:
    """Fiches écrites, triées par identifiant."""
    return sorted(rt.mapping_store.statistics.values(), key=lambda i: i["id"])


def test_pipeline_report_always_carries_the_consumption(settings):
    svc = services(detector=CountingDetector(DICTIONARY), verifier=CountingVerifier())
    report = run_pipeline(make_text_pdf(), settings, svc).report
    usage = report["consommation"]
    assert usage["ia"]["detection"] == CountingDetector.last_stats
    assert usage["ia"]["verification"]["appels"] == report["tours_verification"]  # somme des tours
    assert usage["pages_ocr"] == report["pages"]  # OCR_NATIVE_PAGES : toutes les pages passent à l'OCR
    assert usage["pages_ia"] == report["pages"] == 1  # la page a du texte : envoyée aux agents IA


def test_published_pdf_writes_one_document_item(settings):
    rt = make_runtime(settings, services(classifier=FakeClassifier(scores(("DENTAIRE", 0.9)))))
    assert run_chain(rt, "lot1/facture.pdf")[0] == "ok"
    [item] = items(rt)
    assert item["niveau"] == "document" and item["categorie"] == "pdf" and item["mime"] == "application/pdf"
    assert (item["jour"], item["year"], item["month"], item["day"], item["hour"]) == ("2026-03-14", 2026, 3, 14, 10)
    assert item["statut"] == "ok" and item["domain"] == "dentaire" and item["pages"] == 1
    assert item["toursVerification"] >= 1 and item["residus"] is False
    assert "facture" not in json.dumps(item)  # aucun nom de fichier


def test_file_refused_by_the_router_is_counted_without_pages(settings):
    blobs = FakeBlobService()
    blobs.put("incoming", "lot1/archive.zip", b"PK")
    rt = make_runtime(settings, services(), blobs, MemoryStore())
    assert route_event(event("lot1/archive.zip"), rt).status == "quarantine"
    [item] = [i for i in items(rt) if i["extension"] == ".zip"]
    assert item["categorie"] == "autre" and item["mime"] == "application/zip"
    assert item["statut"] == "quarantine" and item["motifs"] == ["format_non_supporte"] and item["pages"] == 0


def test_redelivery_rewrites_the_item_and_reprocessing_adds_one(settings):
    rt = make_runtime(settings, services())
    status, messages = run_chain(rt, "lot1/facture.pdf")
    assert handle_pdf_job(messages[-1], rt) == "already_processed"
    assert len(items(rt)) == 1  # même job : même fiche
    run_chain(rt, "lot1/facture.pdf")  # retraitement : nouveau job, nouvelle consommation
    assert len(items(rt)) == 2


def test_mail_writes_one_item_for_the_mail_and_one_per_attachment(settings):
    rt = mail_runtime(settings, make_mail())
    assert run_chain(rt, "lot1/mail.eml")[0] == "ok"
    mail, part = items(rt)
    assert mail["niveau"] == "document" and mail["categorie"] == "mail" and mail["mime"] == "message/rfc822"
    assert mail["pages"] == 0 and mail["toursVerification"] >= 1  # le texte du mail est vérifié
    assert part["niveau"] == "piece" and part["categorie"] == "pdf_mail" and part["mime"] == "application/pdf"
    assert part["mailJobId"] == mail["jobId"] and part["pages"] == 1 and part["statut"] == "ok"
    assert part["domain"] == mail["domain"] == "dentaire"  # la pièce suit le classement du mail


def test_mail_quarantined_at_split_counts_refused_and_untouched_attachments(settings):
    mail = make_mail(attachments=(("facture.pdf", make_text_pdf()), ("compte-rendu.docx", b"PK...")))
    rt = mail_runtime(settings, mail)
    assert run_chain(rt, "lot1/mail.eml")[0] == "quarantine"
    by_status = {i["statut"]: i for i in items(rt) if i["niveau"] == "piece"}
    assert by_status["refusee"]["categorie"] == "autre_mail" and by_status["refusee"]["motifs"] == ["format_non_supporte"]
    assert by_status["non_traitee"]["categorie"] == "pdf_mail" and by_status["non_traitee"]["pages"] == 0
    [mail_item] = [i for i in items(rt) if i["niveau"] == "document"]
    assert mail_item["statut"] == "quarantine" and mail_item["consommation"] == empty_consumption()  # aucun appel IA


def test_removed_images_of_a_mail_are_counted_with_their_mime_type(settings):
    msg = message_from_bytes(make_mail(), policy=policy.default)
    msg.get_body(("html",)).add_related(_gif(), maintype="image", subtype="gif", cid="<anim@example.com>")
    msg.add_attachment(_png(250, 800), maintype="image", subtype="png", filename="capture.png")
    rt = mail_runtime(settings, msg.as_bytes())
    assert run_chain(rt, "lot1/mail.eml")[0] == "ok"
    removed = sorted((i["mime"], i["motifs"][0]) for i in items(rt) if i["statut"] == "retiree")
    assert removed == [("image/gif", "gif"), ("image/png", "trop_petites")]
    assert all(i["categorie"] == "image_mail" for i in items(rt) if i["statut"] == "retiree")


def test_json_metadata_is_an_annex_with_its_own_consumption(settings):
    from test_json_metadata import GED

    rt = make_runtime(settings, services())
    rt.blob_service.put("incoming", "lot1/facture.pdf.json", json.dumps(GED).encode())
    from pseudo.text_jobs import handle_text_job

    handle_text_job(route_event(event("lot1/facture.pdf.json"), rt).job.to_json(), rt)
    [item] = items(rt)
    assert item["niveau"] == "annexe" and item["categorie"] == "json" and item["mime"] == "application/json"
    assert "consommation" in item and item["pages"] == 0


def test_statistics_failure_never_blocks_a_document(settings):
    rt = make_runtime(settings, services())

    def broken(items):
        raise RuntimeError("Cosmos indisponible")

    rt.mapping_store.save_statistics = broken
    assert run_chain(rt, "lot1/facture.pdf")[0] == "ok"


def test_consumptions_add_up_and_language_bills_per_1000_characters():
    a = {"pages_ocr": 2, "ia": {"detection": {"appels": 1, "jetons_entree": 100}}}
    b = {"language": {"enregistrements": 3, "caracteres": 2500}, "ia": {"classification": {"appels": 1, "jetons_sortie": 5}}}
    total = merge(a, b, None)
    assert total["pages_ocr"] == 2 and total["language"]["enregistrements"] == 3
    assert total["ia"]["detection"]["jetons_entree"] == 100 and total["ia"]["classification"]["jetons_sortie"] == 5
    assert language_records(["x" * 1000, "x" * 1001, "", "abc"]) == 1 + 2 + 1


def test_period_follows_the_deposit_time_in_paris():
    from pseudo.jobs import Job

    job = Job("j", "d", "incoming", "a.pdf", "pdf", "pdf", received_at="x", deposited_at="2026-12-31T23:30:00+00:00")
    [item] = build_items(job, "ok", {"pages": 2}, None, "Europe/Paris")
    assert (item["jour"], item["hour"]) == ("2027-01-01", 0)


def test_handwritten_document_goes_through_ocr_but_not_ai(monkeypatch):
    from test_handwriting import run_scanned

    result, detector = run_scanned(monkeypatch, "quarantaine")
    usage = result.report["consommation"]
    assert detector.calls == 0 and usage["pages_ocr"] == 1 and usage["pages_ia"] == 0


def test_mail_text_goes_through_ai_without_pages(settings):
    rt = mail_runtime(settings, make_mail())
    run_chain(rt, "lot1/mail.eml")
    mail, part = items(rt)
    assert mail["consommation"]["pages_ia"] == 0 and mail["consommation"]["pages_ocr"] == 0
    assert part["consommation"]["pages_ia"] == 1  # la pièce PDF
