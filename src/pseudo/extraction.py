"""Extraction du texte positionné : couche texte native (PyMuPDF) ou OCR (Document Intelligence).

Toutes les pages passent par l'OCR (OCR_NATIVE_PAGES=true par défaut) :
- page scannée : le texte vient uniquement de l'OCR ;
- page native  : le texte vient de la couche texte du PDF (positions exactes au caractère),
  complété par le texte que l'OCR lit DANS LES IMAGES (logo, tampon, en-tête, carte collée...).
  Les mots OCR qui recouvrent un mot natif sont écartés pour éviter les doublons.

Coordonnées : la rotation des pages est neutralisée en amont (normalize_rotation) ; la page
affichée et les coordonnées PyMuPDF coïncident, et l'OCR est exprimé dans ce même repère (points).
"""

from __future__ import annotations

import logging
from statistics import mean
from typing import Protocol

import pymupdf

from .models import Line, PageContent, Word

log = logging.getLogger(__name__)


def classify_page(page: pymupdf.Page, min_native_chars: int, image_coverage_threshold: float) -> str:
    """'native' si la page a une vraie couche texte, 'ocr' si c'est (surtout) une image."""
    chars = len("".join(page.get_text("text").split()))
    if chars < min_native_chars:
        return "ocr"
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return "native"
    image_area = 0.0
    for info in page.get_image_info():
        x0, y0, x1, y1 = info["bbox"]
        image_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    # Une image couvrant la page avec du texte = scan avec couche OCR invisible : on ré-OCRise
    # et on rastérise, car la couche existante est de qualité inconnue.
    return "ocr" if image_area / page_area >= image_coverage_threshold else "native"


def extract_native_page(page: pymupdf.Page) -> PageContent:
    groups: dict[tuple[int, int], list[tuple]] = {}
    for w in page.get_text("words", sort=True):
        groups.setdefault((w[5], w[6]), []).append(w)
    lines: list[Line] = []
    for n, items in enumerate(groups.values(), start=1):
        items.sort(key=lambda w: w[7])
        words = [Word(text=w[4], rect=(w[0], w[1], w[2], w[3])) for w in items]
        lines.append(Line.build(f"p{page.number + 1}-l{n}", page.number, words))
    return PageContent(index=page.number, source="native", lines=lines)


class OcrEngine(Protocol):
    def ocr(self, pdf_bytes: bytes, doc: pymupdf.Document, page_indexes: list[int]) -> list[PageContent]:
        ...


class DocumentIntelligenceOcr:
    """OCR des pages scannées via Azure AI Document Intelligence (modèle prebuilt-read)."""

    def __init__(self, client, model_id: str = "prebuilt-read"):
        self._client = client
        self._model_id = model_id

    def ocr(self, pdf_bytes: bytes, doc: pymupdf.Document, page_indexes: list[int]) -> list[PageContent]:
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest

        if not page_indexes:
            return []
        pages_arg = ",".join(str(i + 1) for i in sorted(page_indexes))
        poller = self._client.begin_analyze_document(
            self._model_id, AnalyzeDocumentRequest(bytes_source=pdf_bytes), pages=pages_arg
        )
        result = poller.result()
        return [build_ocr_page(doc[p.page_number - 1], p) for p in (result.pages or [])]


def build_ocr_page(page: pymupdf.Page, di_page) -> PageContent:
    """Convertit une page Document Intelligence en PageContent (coordonnées affichées, en points)."""
    sx = page.rect.width / di_page.width if di_page.width else 1.0
    sy = page.rect.height / di_page.height if di_page.height else 1.0
    di_words = list(di_page.words or [])
    lines: list[Line] = []
    used: set[int] = set()
    for n, di_line in enumerate(di_page.lines or [], start=1):
        ranges = [(s.offset, s.offset + s.length) for s in (di_line.spans or [])]
        words: list[Word] = []
        for i, w in enumerate(di_words):
            if i in used or w.span is None:
                continue
            if any(a <= w.span.offset < b for a, b in ranges):
                used.add(i)
                words.append(_di_word(w, sx, sy))
        if words:
            lines.append(Line.build(f"p{page.number + 1}-l{n}", page.number, words, source="ocr"))
    # Mots hors de toute ligne (rare) : on les garde dans une ligne dédiée pour ne rien perdre.
    orphans = [_di_word(w, sx, sy) for i, w in enumerate(di_words) if i not in used]
    if orphans:
        lines.append(Line.build(f"p{page.number + 1}-l{len(lines) + 1000}", page.number, orphans, source="ocr"))
    confidences = [w.confidence for line in lines for w in line.words]
    return PageContent(
        index=page.number,
        source="ocr",
        lines=lines,
        ocr_confidence=mean(confidences) if confidences else 0.0,
    )


def _di_word(w, sx: float, sy: float) -> Word:
    poly = list(w.polygon or [])
    xs, ys = poly[0::2], poly[1::2]
    rect = (min(xs) * sx, min(ys) * sy, max(xs) * sx, max(ys) * sy) if xs else (0, 0, 0, 0)
    conf = w.confidence if w.confidence is not None else 1.0
    return Word(text=w.content, rect=rect, confidence=conf)


def image_text_lines(native: PageContent, ocr_page: PageContent, overlap_threshold: float = 0.5) -> list[Line]:
    """Lignes OCR d'une page native absentes de la couche texte, c.-à-d. le texte contenu dans les images."""
    native_rects = [w.rect for line in native.lines for w in line.words]
    lines: list[Line] = []
    for n, ocr_line in enumerate(ocr_page.lines, start=1):
        kept = [w for w in ocr_line.words if _overlap_ratio(w.rect, native_rects) < overlap_threshold]
        if kept:
            lines.append(Line.build(f"p{native.index + 1}-i{n}", native.index, kept, source="ocr"))
    return lines


def _overlap_ratio(rect, others) -> float:
    """Plus forte proportion de la surface de rect recouverte par l'un des autres rectangles."""
    x0, y0, x1, y1 = rect
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if area <= 0:
        return 1.0
    best = 0.0
    for a0, b0, a1, b1 in others:
        w = min(x1, a1) - max(x0, a0)
        h = min(y1, b1) - max(y0, b0)
        if w > 0 and h > 0:
            best = max(best, w * h / area)
    return best


def extract_document(
    pdf_bytes: bytes,
    doc: pymupdf.Document,
    ocr: OcrEngine | None,
    *,
    min_native_chars: int,
    image_coverage_threshold: float,
    force_ocr: bool,
    ocr_native_pages: bool = True,
) -> list[PageContent]:
    native: dict[int, PageContent] = {}
    scanned: list[int] = []
    for page in doc:
        kind = "ocr" if force_ocr else classify_page(page, min_native_chars, image_coverage_threshold)
        if kind == "native":
            native[page.number] = extract_native_page(page)
        else:
            scanned.append(page.number)

    to_ocr = list(range(doc.page_count)) if ocr_native_pages else scanned
    ocr_results: dict[int, PageContent] = {}
    if to_ocr:
        if ocr is None:
            raise RuntimeError("OCR requis (pages scannées ou OCR_NATIVE_PAGES) mais aucun moteur configuré")
        log.info("OCR de %d page(s) dont %d scannée(s)", len(to_ocr), len(scanned))
        ocr_results = {p.index: p for p in ocr.ocr(pdf_bytes, doc, to_ocr)}

    pages: list[PageContent] = []
    for idx in range(doc.page_count):
        if idx in native:
            page = native[idx]
            if idx in ocr_results:
                extra = image_text_lines(page, ocr_results[idx])
                page.lines += extra
                if extra:
                    page.ocr_confidence = mean(w.confidence for line in extra for w in line.words)
            pages.append(page)
        else:
            # Une page sans résultat OCR (page blanche) reste traitée comme scannée, sans texte.
            pages.append(ocr_results.get(idx, PageContent(index=idx, source="ocr", lines=[], ocr_confidence=None)))
    return pages
