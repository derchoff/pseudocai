"""Production du PDF pseudonymisé.

- Pages natives : annotations de caviardage PyMuPDF puis apply_redactions(), qui SUPPRIME
  réellement le texte sous-jacent (pas un simple rectangle posé dessus).
- Pages scannées : la page est re-rastérisée, les zones sont effacées dans les pixels, puis le
  jeton est écrit par-dessus. Aucune couche texte/OCR d'origine n'est conservée.
Le document de sortie est reconstruit page par page : métadonnées, pièces jointes, liens,
annotations et signets d'origine ne sont pas recopiés.
"""

from __future__ import annotations

from collections import defaultdict

import pymupdf

from .models import DocumentContent, Line, Span

FILL_RGB = (0.85, 0.85, 0.85)
TEXT_RGB = (0.0, 0.0, 0.0)
FONT = "helv"
MARGIN = 0.8  # points


def _fontsize(token: str, rect: pymupdf.Rect) -> float:
    unit_width = pymupdf.get_text_length(token, fontname=FONT, fontsize=1)
    size = min(rect.height * 0.7, rect.width * 0.95 / unit_width if unit_width else rect.height)
    return max(size, 2.0)


def normalize_rotation(doc: pymupdf.Document) -> None:
    """Supprime /Rotate en conservant l'apparence : un seul repère de coordonnées pour tout le pipeline."""
    for page in doc:
        if page.rotation and hasattr(page, "remove_rotation"):
            page.remove_rotation()


def write_token(page: pymupdf.Page, rect: pymupdf.Rect, token: str) -> None:
    size = _fontsize(token, rect)
    width = pymupdf.get_text_length(token, fontname=FONT, fontsize=size)
    x = rect.x0 + max(0.0, (rect.width - width) / 2)
    y = rect.y0 + (rect.height + size * 0.7) / 2
    page.insert_text((x, y), token, fontname=FONT, fontsize=size, color=TEXT_RGB)


def _words_rect(line: Line, span: Span) -> pymupdf.Rect | None:
    rect = None
    for w in line.words:
        if w.start < span.end and w.end > span.start:
            r = pymupdf.Rect(w.rect)
            rect = r if rect is None else rect | r
    return rect


def span_rects(page: pymupdf.Page | None, line: Line, span: Span) -> list[pymupdf.Rect]:
    """Rectangles à masquer pour une zone. Précis au caractère pour le natif, au mot pour l'OCR."""
    words_rect = _words_rect(line, span)
    if words_rect is None:
        return []
    if page is not None and line.source == "native":
        text = span.text(line)
        clip = pymupdf.Rect(words_rect) + (-1, -1, 1, 1)
        hits = page.search_for(text, clip=clip) if text.strip() else []
        if hits:
            return [pymupdf.Rect(h) + (-MARGIN, -MARGIN, MARGIN, MARGIN) for h in hits]
    return [words_rect + (-MARGIN, -MARGIN, MARGIN, MARGIN)]


def render_pdf(pdf_bytes: bytes, content: DocumentContent, spans: list[Span], dpi: int = 200) -> bytes:
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    # Mêmes préparations que lors de l'extraction, pour que les coordonnées correspondent.
    if hasattr(src, "bake"):
        src.bake(annots=True, widgets=True)
    normalize_rotation(src)
    by_page: dict[int, list[tuple[Line, Span]]] = defaultdict(list)
    for s in spans:
        line = content.line(s.line_id)
        if line is not None:
            by_page[line.page].append((line, s))

    out = pymupdf.open()
    for pc in content.pages:
        page = src[pc.index]
        items = by_page.get(pc.index, [])
        if pc.source == "native":
            _redact_native(page, items)
            out.insert_pdf(src, from_page=pc.index, to_page=pc.index, links=False, annots=False)
        else:
            _rebuild_scanned(out, page, items, dpi)
    out.set_metadata({})
    data = out.tobytes(garbage=4, deflate=True, clean=True)
    out.close()
    src.close()
    return data


def _redact_native(page: pymupdf.Page, items: list[tuple[Line, Span]]) -> None:
    if not items:
        return
    boxes: list[tuple[pymupdf.Rect, str]] = []
    for line, span in items:
        for rect in span_rects(page, line, span):
            page.add_redact_annot(rect, fill=FILL_RGB, cross_out=False)
            boxes.append((rect, span.token))
    # Supprime réellement le texte, les pixels des images (texte lu par OCR dans un logo, un
    # tampon...) et les tracés vectoriels entièrement contenus dans les zones.
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
        graphics=getattr(pymupdf, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", 1),
    )
    for rect, token in boxes:
        write_token(page, rect, token)


def _rebuild_scanned(out: pymupdf.Document, page: pymupdf.Page, items: list[tuple[Line, Span]], dpi: int) -> None:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    scale = dpi / 72.0
    fill = tuple(int(c * 255) for c in FILL_RGB)
    boxes: list[tuple[pymupdf.Rect, str]] = []
    for line, span in items:
        for rect in span_rects(None, line, span):
            irect = pymupdf.IRect(
                max(0, int(rect.x0 * scale)),
                max(0, int(rect.y0 * scale)),
                min(pix.width, int(rect.x1 * scale) + 1),
                min(pix.height, int(rect.y1 * scale) + 1),
            )
            if not irect.is_empty:
                pix.set_rect(irect, fill)
            boxes.append((rect, span.token))
    new_page = out.new_page(width=page.rect.width, height=page.rect.height)
    new_page.insert_image(new_page.rect, pixmap=pix)
    for rect, token in boxes:
        write_token(new_page, rect, token)
