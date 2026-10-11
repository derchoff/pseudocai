"""Tests de bout en bout du pipeline avec de faux agents IA et un faux OCR (aucun appel Azure)."""

import pymupdf
import pytest

from pseudo.config import Settings
from pseudo.extraction import extract_native_page
from pseudo.models import Entity
from pseudo.pipeline import Services, run_pipeline
from pseudo.pseudonymizer import Pseudonymizer, ValueCipher
from pseudo.surrogates import SurrogateFactory

LINES = [
    "Attestation",
    "Patient : M. DUPONT Jean",
    "Ne le 12/03/1985",
    "12 rue des Lilas 75011 PARIS",
    "Tel 06 12 34 56 78",
    "Signature : DUPONT",
]
DICTIONARY = {"DUPONT": "NOM", "Jean": "PRENOM"}
FILL = 217  # 0.85 * 255


class FakeDetector:
    prompt_sha256 = "fake"

    def __init__(self, dictionary, only_first=True):
        self.dictionary = dictionary
        self.only_first = only_first

    def detect(self, lines):
        out, seen = [], set()
        for line in lines:
            for value, etype in self.dictionary.items():
                if value in line.text and not (self.only_first and value in seen):
                    seen.add(value)
                    out.append(Entity(line.id, value, etype))
        return out


class FakeVerifier:
    prompt_sha256 = "fake"

    def __init__(self, residual_text=None, residual_type="ADRESSE"):
        self.residual_text = residual_text
        self.residual_type = residual_type
        self.calls = 0

    def verify(self, lines):
        self.calls += 1
        if self.residual_text is None:
            return []
        return [
            Entity(line_id, self.residual_text, self.residual_type, source="verifier")
            for line_id, text in lines
            if self.residual_text in text
        ]


def make_text_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(LINES):
        page.insert_text((72, 72 + 24 * i), text, fontsize=12)
    return doc.tobytes()


def make_scanned_pdf() -> tuple[bytes, bytes]:
    """Page image (pas de couche texte) + le PDF texte équivalent (ce que « voit » l'OCR)."""
    truth = make_text_pdf()
    text_doc = pymupdf.open(stream=truth, filetype="pdf")
    pix = text_doc[0].get_pixmap(dpi=150)
    scan = pymupdf.open()
    page = scan.new_page(width=text_doc[0].rect.width, height=text_doc[0].rect.height)
    page.insert_image(page.rect, pixmap=pix)
    return scan.tobytes(), truth


LOGO_RECT = pymupdf.Rect(60, 380, 330, 415)
LOGO_TEXT = "Plomberie MARTIN"


def make_pdf_with_logo() -> tuple[bytes, bytes]:
    """Page native dont l'en-tête « Plomberie MARTIN » est une IMAGE (logo), + la vérité OCR."""
    full = pymupdf.open()
    page = full.new_page()
    for i, text in enumerate(LINES):
        page.insert_text((72, 72 + 24 * i), text, fontsize=12)
    page.insert_text((72, 405), LOGO_TEXT, fontsize=20)
    truth = full.tobytes()
    logo = page.get_pixmap(dpi=150, clip=LOGO_RECT)

    real = pymupdf.open()
    real_page = real.new_page()
    for i, text in enumerate(LINES):
        real_page.insert_text((72, 72 + 24 * i), text, fontsize=12)
    real_page.insert_image(LOGO_RECT, pixmap=logo)
    return real.tobytes(), truth


class TruthOcr:
    """Simule Document Intelligence : renvoie tout le texte visible de la page (natif ET images)."""

    def __init__(self, truth_pdf: bytes, confidence: float = 0.95):
        self.truth_pdf = truth_pdf
        self.confidence = confidence
        self.calls: list[list[int]] = []

    def ocr(self, pdf_bytes, doc, page_indexes):
        self.calls.append(list(page_indexes))
        truth = pymupdf.open(stream=self.truth_pdf, filetype="pdf")
        pages = []
        for i in page_indexes:
            pc = extract_native_page(truth[i])
            pc.source = "ocr"
            pc.ocr_confidence = self.confidence
            for line in pc.lines:
                line.source = "ocr"
                for w in line.words:
                    w.confidence = self.confidence
            pages.append(pc)
        return pages


@pytest.fixture
def settings(monkeypatch):
    for key in ("FORCE_OCR", "VERIFICATION_ROUNDS", "OCR_NATIVE_PAGES"):
        monkeypatch.delenv(key, raising=False)
    return Settings.from_env()


def services(**kwargs) -> Services:
    defaults = dict(
        detector=FakeDetector(DICTIONARY),
        pseudonymizer=Pseudonymizer(b"k" * 32),
        cipher=ValueCipher(b"e" * 32),
        verifier=FakeVerifier(),
        ocr=TruthOcr(make_text_pdf()),
        surrogates=SurrogateFactory(b"k" * 32, current_year=2026),
    )
    defaults.update(kwargs)
    return Services(**defaults)


def output_text(pdf: bytes) -> str:
    return "".join(page.get_text() for page in pymupdf.open(stream=pdf, filetype="pdf"))


def test_native_pdf_is_pseudonymised(settings):
    svc = services()
    result = run_pipeline(make_text_pdf(), settings, svc)
    assert result.status == "ok", result.reasons
    text = output_text(result.output)
    for secret in ("DUPONT", "Jean", "12/03/1985", "06 12 34 56 78"):
        assert secret not in text
        assert secret not in result.text
    token = svc.pseudonymizer.token("NOM", "DUPONT")
    # Propagation : la 2e occurrence (signature), non signalée par l'agent, est aussi remplacée.
    assert result.text.count(token) == 2
    assert result.report["pages_natives"] == 1
    # L'OCR a bien été appelé sur la page native, mais son texte (doublon de la couche texte)
    # n'a pas été ajouté.
    assert svc.ocr.calls == [[0]]
    assert result.report["lignes_texte_images"] == 0
    assert result.text.count("Attestation") == 1
    assert {r.token for r in result.records} >= {token}
    assert "Attestation" in text


def test_verifier_residual_is_redacted(settings):
    verifier = FakeVerifier(residual_text="12 rue des Lilas 75011 PARIS")
    result = run_pipeline(make_text_pdf(), settings, services(verifier=verifier))
    # 1er tour : résidu trouvé et masqué ; 2e tour : plus rien en clair -> statut ok.
    assert result.status == "ok", result.reasons
    assert "Lilas" not in output_text(result.output)
    assert "Lilas" not in result.text
    assert verifier.calls == 2


def test_unlocated_entity_sends_to_quarantine(settings):
    detector = FakeDetector({"INTROUVABLE": "NOM"})
    detector.detect = lambda lines: [Entity("p1-l2", "INTROUVABLE", "NOM")]
    result = run_pipeline(make_text_pdf(), settings, services(detector=detector))
    assert result.status == "quarantine"
    assert "entites_non_localisees" in result.reasons


def test_scanned_pdf_is_rasterised_and_masked(settings):
    pdf, truth_pdf = make_scanned_pdf()
    svc = services(ocr=TruthOcr(truth_pdf))
    result = run_pipeline(pdf, settings, svc)
    assert result.status == "ok", result.reasons
    assert result.report["pages_ocr"] == 1
    out = pymupdf.open(stream=result.output, filetype="pdf")
    page_text = out[0].get_text()
    assert svc.pseudonymizer.token("NOM", "DUPONT") in page_text
    # Le pixel au coin du mot « DUPONT » doit être recouvert par la couleur de masquage.
    truth = extract_native_page(pymupdf.open(stream=truth_pdf, filetype="pdf")[0])
    word = next(w for line in truth.lines for w in line.words if w.text == "DUPONT")
    pix = out[0].get_pixmap(dpi=72)
    x, y = int(word.rect[0]) + 1, int(word.rect[1]) + 1
    assert all(abs(c - FILL) <= 3 for c in pix.pixel(x, y)[:3])


def test_encrypted_pdf_goes_to_quarantine(settings):
    doc = pymupdf.open(stream=make_text_pdf(), filetype="pdf")
    encrypted = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    result = run_pipeline(encrypted, settings, services())
    assert result.status == "quarantine"
    assert result.reasons == ["pdf_chiffre"]


def test_rotated_native_page_is_pseudonymised(settings):
    doc = pymupdf.open(stream=make_text_pdf(), filetype="pdf")
    doc[0].set_rotation(90)
    rotated = doc.tobytes()
    # Ce que voit l'OCR : la page telle qu'affichée (rotation appliquée).
    upright = pymupdf.open(stream=rotated, filetype="pdf")
    upright[0].remove_rotation()
    result = run_pipeline(rotated, settings, services(ocr=TruthOcr(upright.tobytes())))
    assert result.status == "ok", result.reasons
    text = output_text(result.output)
    assert "DUPONT" not in text and "06 12 34 56 78" not in text
    assert "[NOM_" in text


def test_text_inside_image_on_native_page_is_pseudonymised(settings):
    pdf, truth_pdf = make_pdf_with_logo()
    svc = services(
        detector=FakeDetector({**DICTIONARY, "MARTIN": "NOM"}),
        ocr=TruthOcr(truth_pdf),
    )
    result = run_pipeline(pdf, settings, svc)
    assert result.status == "ok", result.reasons

    # La page reste native (couche texte conservée) ...
    assert result.report["pages_natives"] == 1
    assert "Attestation" in output_text(result.output)
    # ... et le texte du logo, lu par OCR, est détecté puis pseudonymisé.
    assert result.report["lignes_texte_images"] == 1
    assert "MARTIN" not in result.text
    assert svc.pseudonymizer.token("NOM", "MARTIN") in result.text
    assert svc.pseudonymizer.token("NOM", "MARTIN") in output_text(result.output)

    # Les pixels du mot « MARTIN » dans l'image sont effacés.
    truth = extract_native_page(pymupdf.open(stream=truth_pdf, filetype="pdf")[0])
    word = next(w for line in truth.lines for w in line.words if w.text == "MARTIN")
    pix = pymupdf.open(stream=result.output, filetype="pdf")[0].get_pixmap(dpi=72)
    x, y = int(word.rect[0]) + 1, int(word.rect[1]) + 1
    assert all(abs(c - FILL) <= 3 for c in pix.pixel(x, y)[:3])
    # « Plomberie » (non personnel) reste visible dans l'image : pixels non uniformes.
    plomberie = next(w for line in truth.lines for w in line.words if w.text == "Plomberie")
    clip = pymupdf.Rect(plomberie.rect)
    region = pymupdf.open(stream=result.output, filetype="pdf")[0].get_pixmap(dpi=72, clip=clip)
    assert len(set(region.samples)) > 2


def test_ocr_native_pages_can_be_disabled(settings, monkeypatch):
    monkeypatch.setenv("OCR_NATIVE_PAGES", "false")
    svc = services()
    result = run_pipeline(make_text_pdf(), Settings.from_env(), svc)
    assert result.status == "ok"
    assert svc.ocr.calls == []
