"""Écriture manuscrite : détection par l'OCR et politique HANDWRITING_POLICY."""

from types import SimpleNamespace as NS

import pymupdf
import pytest

from pseudo.config import Settings
from pseudo.extraction import build_ocr_page, handwritten_ranges
from pseudo.pipeline import run_pipeline
from test_pipeline import FakeDetector, TruthOcr, make_scanned_pdf, services


def di_result():
    """Résultat Document Intelligence minimal : « Patient : DUPONT », DUPONT écrit à la main."""
    words = [
        NS(content="Patient", polygon=[10, 10, 60, 10, 60, 20, 10, 20], confidence=0.99, span=NS(offset=0, length=7)),
        NS(content=":", polygon=[62, 10, 65, 10, 65, 20, 62, 20], confidence=0.99, span=NS(offset=8, length=1)),
        NS(content="DUPONT", polygon=[70, 10, 120, 10, 120, 20, 70, 20], confidence=0.6, span=NS(offset=10, length=6)),
    ]
    page = NS(page_number=1, width=595, height=842, words=words,
              lines=[NS(spans=[NS(offset=0, length=16)])])
    styles = [
        NS(is_handwritten=True, confidence=0.9, spans=[NS(offset=10, length=6)]),
        NS(is_handwritten=True, confidence=0.2, spans=[NS(offset=0, length=7)]),  # sous le seuil
    ]
    return NS(pages=[page], styles=styles)


def test_handwritten_words_are_flagged_from_document_intelligence_styles():
    result = di_result()
    ranges = handwritten_ranges(result, min_confidence=0.5)
    assert ranges == [(10, 16)]
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    page = build_ocr_page(doc[0], result.pages[0], ranges)
    assert {w.text: w.handwritten for line in page.lines for w in line.words} == {
        "Patient": False, ":": False, "DUPONT": True,
    }


class HandwrittenOcr(TruthOcr):
    """OCR simulé : les deux « DUPONT » du document sont signalés comme manuscrit."""

    def ocr(self, pdf_bytes, doc, page_indexes):
        pages = super().ocr(pdf_bytes, doc, page_indexes)
        for page in pages:
            for line in page.lines:
                for w in line.words:
                    w.handwritten = w.text == "DUPONT"
        return pages


class CountingDetector(FakeDetector):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0

    def detect(self, lines):
        self.calls += 1
        return super().detect(lines)


def run_scanned(monkeypatch, policy):
    monkeypatch.setenv("HANDWRITING_POLICY", policy)
    pdf, truth_pdf = make_scanned_pdf()
    detector = CountingDetector({"DUPONT": "NOM", "Jean": "PRENOM"})
    result = run_pipeline(pdf, Settings.from_env(), services(ocr=HandwrittenOcr(truth_pdf), detector=detector))
    return result, detector


def test_handwriting_sends_to_quarantine_before_any_ai_call(monkeypatch):
    result, detector = run_scanned(monkeypatch, "quarantaine")
    assert result.status == "quarantine" and result.reasons == ["manuscrit_detecte"]
    assert result.report["mots_manuscrits"] == 2 and result.report["pages_manuscrites"] == [1]
    assert detector.calls == 0  # rien n'a été envoyé aux agents IA
    assert result.output is None


def test_handwriting_is_processed_when_policy_allows_it(monkeypatch):
    result, detector = run_scanned(monkeypatch, "traiter")
    assert result.status == "ok", result.reasons
    assert result.report["mots_manuscrits"] == 2 and result.report["politique_manuscrit"] == "traiter"
    assert detector.calls == 1


def test_printed_document_is_not_affected(monkeypatch):
    monkeypatch.setenv("HANDWRITING_POLICY", "quarantaine")
    pdf, truth_pdf = make_scanned_pdf()
    result = run_pipeline(pdf, Settings.from_env(), services(ocr=TruthOcr(truth_pdf)))
    assert result.status == "ok" and result.report["mots_manuscrits"] == 0


def test_default_policy_is_quarantine_and_unknown_values_are_rejected(monkeypatch):
    monkeypatch.delenv("HANDWRITING_POLICY", raising=False)
    assert Settings.from_env().handwriting_policy == "quarantaine"
    monkeypatch.setenv("HANDWRITING_POLICY", "ignorer")
    with pytest.raises(ValueError, match="HANDWRITING_POLICY"):
        Settings.from_env()
