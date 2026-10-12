"""Storage d'origine distinct du compte « data » (SOURCE_STORAGE_ACCOUNT_URL)."""

import pytest

from fakes import FakeBlobService, MemoryStore, event, run_chain
from pseudo.config import Settings
from pseudo.router import route_event
from pseudo.runtime import Runtime, _source_blob_service
from test_pipeline import make_text_pdf, services

DATA_URL = "https://stdata.blob.core.windows.net/"
SOURCE_URL = "https://stmetier.blob.core.windows.net/"


@pytest.fixture
def settings(monkeypatch):
    """Compte d'origine « stmetier », container « factures-entrantes »."""
    monkeypatch.setenv("STORAGE_ACCOUNT_URL", DATA_URL)
    monkeypatch.setenv("SOURCE_STORAGE_ACCOUNT_URL", SOURCE_URL)
    monkeypatch.setenv("INCOMING_CONTAINER", "factures-entrantes")
    return Settings.from_env()


def runtime(settings):
    """Runtime avec deux comptes : la facture est déposée dans le compte d'origine."""
    data, source = FakeBlobService(), FakeBlobService()
    source.put("factures-entrantes", "lot1/facture.pdf", make_text_pdf())
    return Runtime(settings=settings, services=services(), mapping_store=MemoryStore(),
                   blob_service=data, source_blob_service=source)


def test_document_is_read_from_the_source_account_and_published_in_data(settings):
    rt = runtime(settings)
    status, _ = run_chain(rt, "lot1/facture.pdf")
    assert status == "ok"
    assert "lot1/facture.pdf" in rt.blob_service.names("pseudonymised")
    assert rt.source_blob_service.names("factures-entrantes") == ["lot1/facture.pdf"]  # source intacte
    assert rt.blob_service.names("factures-entrantes") == []  # rien d'écrit dans data côté entrée


def test_source_is_deleted_in_the_source_account_when_requested(settings, monkeypatch):
    monkeypatch.setenv("DELETE_SOURCE_AFTER_SUCCESS", "true")
    rt = runtime(Settings.from_env())
    assert run_chain(rt, "lot1/facture.pdf")[0] == "ok"
    assert rt.source_blob_service.names("factures-entrantes") == []


def test_event_from_another_account_is_ignored(settings):
    rt = runtime(settings)
    other = event("lot1/facture.pdf", "factures-entrantes", host="stautre.blob.core.windows.net")
    assert route_event(other, rt).status == "ignored"


def test_same_account_uses_a_single_client(monkeypatch):
    monkeypatch.setenv("STORAGE_ACCOUNT_URL", DATA_URL)
    monkeypatch.delenv("SOURCE_STORAGE_ACCOUNT_URL", raising=False)
    assert _source_blob_service(Settings.from_env(), cred=None) is None
    monkeypatch.setenv("SOURCE_STORAGE_ACCOUNT_URL", DATA_URL.upper())
    assert _source_blob_service(Settings.from_env(), cred=None) is None
