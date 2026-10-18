"""Dossiers de sortie year=/month=/day=/hour=/domain= d'après la date de dépose dans incoming/."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fakes import DEPOSIT as D, FakeBlobService, MemoryStore, event, run_chain
from pseudo.config import Settings
from pseudo.jobs import Job
from pseudo.outputs import deposit_folder
from pseudo.router import _deposited_at, route_event
from test_classification import FakeClassifier, make_runtime, scores
from test_pipeline import make_text_pdf, services


@pytest.fixture
def settings(monkeypatch):
    """Réglages par défaut (fuseau Europe/Paris)."""
    monkeypatch.delenv("DEPOSIT_TIMEZONE", raising=False)
    return Settings.from_env()


def job_deposited(when: str) -> Job:
    """Job d'un document déposé à la date donnée (ISO 8601)."""
    return Job("j", "d", "incoming", "lot1/facture.pdf", "pdf", "pdf", received_at=when, deposited_at=when)


@pytest.mark.parametrize(
    ("deposited", "expected"),
    [
        ("2026-03-14T09:26:00+00:00", "year=2026/month=03/day=14/hour=10"),  # hiver : UTC+1
        ("2026-07-01T09:26:00+00:00", "year=2026/month=07/day=01/hour=11"),  # été : UTC+2
        ("2026-12-31T23:30:00+00:00", "year=2027/month=01/day=01/hour=00"),  # passage à l'année suivante à Paris
    ],
)
def test_folder_uses_the_deposit_date_in_paris_time(settings, deposited, expected):
    assert deposit_folder(job_deposited(deposited), "dentaire", settings) == f"{expected}/domain=dentaire"


def test_timezone_is_configurable(monkeypatch):
    monkeypatch.setenv("DEPOSIT_TIMEZONE", "UTC")
    folder = deposit_folder(job_deposited("2026-03-14T09:26:00+00:00"), "dentaire", Settings.from_env())
    assert folder == "year=2026/month=03/day=14/hour=09/domain=dentaire"


def test_without_type_folder_the_domain_level_is_omitted(settings):
    assert deposit_folder(job_deposited("2026-03-14T09:26:00+00:00"), "", settings) == "year=2026/month=03/day=14/hour=10"


def test_job_from_a_previous_version_falls_back_to_its_routing_time(settings):
    old = Job.from_json(
        '{"job_id": "j", "document_id": "d", "source_container": "incoming", "source_name": "a.pdf",'
        ' "source_format": "pdf", "origin": "pdf", "received_at": "2026-03-14T09:26:00+00:00"}'
    )
    assert deposit_folder(old, "optique", settings) == "year=2026/month=03/day=14/hour=10/domain=optique"


def test_router_reads_the_deposit_date_from_the_blob(settings):
    assert _deposited_at(SimpleNamespace(last_modified=datetime(2026, 5, 2, 7, 0, tzinfo=timezone.utc))) == "2026-05-02T07:00:00+00:00"
    assert _deposited_at(SimpleNamespace(last_modified=None, creation_time=None)) is None
    blobs = FakeBlobService()
    blobs.put("incoming", "lot1/facture.pdf", make_text_pdf(), deposited=datetime(2026, 5, 2, 7, 0, tzinfo=timezone.utc))
    rt = make_runtime(settings, services(), blobs, MemoryStore())
    assert route_event(event("lot1/facture.pdf"), rt).job.deposited_at == "2026-05-02T07:00:00+00:00"


def test_redeposited_document_moves_to_the_new_date_folder(settings):
    blobs, store = FakeBlobService(), MemoryStore()
    classifier = FakeClassifier(scores(("DENTAIRE", 0.9)))
    run_chain(make_runtime(settings, services(classifier=classifier), blobs, store), "lot1/facture.pdf")
    assert all(n.startswith(f"{D}/domain=dentaire/lot1/") for n in blobs.names("pseudonymised"))
    # Même fichier redéposé le lendemain : nouveau dossier, anciennes sorties supprimées.
    blobs.put("incoming", "lot1/facture.pdf", make_text_pdf(), deposited=datetime(2026, 3, 15, 8, 5, tzinfo=timezone.utc))
    run_chain(make_runtime(settings, services(classifier=classifier), blobs, store), "lot1/facture.pdf")
    names = blobs.names("pseudonymised")
    assert len(names) == 3
    assert all(n.startswith("year=2026/month=03/day=15/hour=09/domain=dentaire/lot1/") for n in names)
