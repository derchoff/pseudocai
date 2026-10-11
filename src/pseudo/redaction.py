"""Production du PDF pseudonymisé.

- Pages natives : annotations de caviardage PyMuPDF puis apply_redactions(), qui SUPPRIME
  réellement le texte sous-jacent (pas un simple rectangle posé dessus).
- Pages scannées : la page est re-rastérisée, les zones sont effacées dans les pixels, puis le
  remplacement est écrit par-dessus. Aucune couche texte/OCR d'origine n'est conservée.
Le remplacement est soit le jeton (mode « jetons » : zone grise, jeton centré), soit la valeur
fictive (mode « substitution » : zone de la couleur du fond, texte noir aligné à gauche, pour
un document lisible par un OCR).
Le document de sortie est reconstruit page par page : métadonnées, pièces jointes, liens,
annotations et signets d'origine ne sont pas recopiés.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

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


@dataclass
class TextStyle:
    """Style du texte d'origine sous une zone : taille, ligne de base, graisse."""

    size: float
    baseline: float
    bold: bool = False


def text_style(page: pymupdf.Page, rect: pymupdf.Rect) -> TextStyle | None:
    """Style du texte natif présent dans la zone (lu AVANT caviardage), ou None."""
    # Seuls les fragments de la MÊME ligne comptent : leur centre vertical est dans le cœur de la
    # zone. Les lignes voisines, dont les boîtes débordent sur la zone, sont ignorées.
    core = _core(rect)
    best, best_overlap = None, 0.0
    for block in page.get_text("dict", clip=rect).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                x0, y0, x1, y1 = span["bbox"]
                if not span.get("text", "").strip() or not core.y0 <= (y0 + y1) / 2 <= core.y1:
                    continue
                overlap = max(0.0, min(x1, rect.x1) - max(x0, rect.x0))
                if overlap > best_overlap:
                    best, best_overlap = span, overlap
    if best is None:
        return None
    return TextStyle(size=best["size"], baseline=best["origin"][1], bold=bool(best["flags"] & 16))


def write_surrogate(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    style: TextStyle | None = None,
    max_width: float | None = None,
) -> None:
    """Valeur fictive écrite comme du texte ordinaire, dans le style de l'original (taille, ligne
    de base, gras). Elle peut déborder jusqu'au mot suivant de la ligne avant d'être réduite."""
    font = "hebo" if style and style.bold else FONT
    size = style.size if style else max((rect.height - 2 * MARGIN) * 0.8, 2.0)
    baseline = style.baseline if style else rect.y1 - MARGIN - (rect.height - 2 * MARGIN) * 0.22
    available = max(max_width or rect.width, rect.width) - 0.6
    width = pymupdf.get_text_length(text, fontname=font, fontsize=size)
    if width > available > 0:
        size = max(size * available / width, 2.0)
    page.insert_text((rect.x0 + MARGIN, baseline), text, fontname=font, fontsize=size, color=TEXT_RGB)


def write_replacement(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    span: Span,
    style: TextStyle | None = None,
    max_width: float | None = None,
) -> None:
    if span.surrogate is not None:
        write_surrogate(page, rect, span.surrogate, style, max_width)
    else:
        write_token(page, rect, span.token)


def available_width(line: Line, span: Span, rect: pymupdf.Rect, page_width: float) -> float:
    """Largeur disponible pour la valeur fictive : jusqu'au mot suivant de la ligne, sinon une
    fois et demie la zone (sans dépasser la page)."""
    following = [w.rect[0] for w in line.words if w.start >= span.end and w.rect[0] > rect.x0]
    limit = min(following) - 1.5 if following else min(rect.x0 + rect.width * 1.5, page_width - 10)
    return max(limit - rect.x0, rect.width)


def ring_color(pixel, x0: int, y0: int, x1: int, y1: int, width: int, height: int) -> tuple[int, int, int]:
    """Couleur du fond autour d'une zone : couleur la PLUS FRÉQUENTE des pixels du pourtour. Le
    texte voisin ou un trait de cadre qui longe un côté sont ainsi ignorés. `pixel(x, y)` renvoie
    (r, g, b) en 0-255."""
    x0, y0 = max(0, x0 - 2), max(0, y0 - 2)
    x1, y1 = min(width - 1, x1 + 2), min(height - 1, y1 + 2)
    step = max(1, (x1 - x0 + y1 - y0) // 200)
    points = [(x, y) for x in range(x0, x1 + 1, step) for y in (y0, y1)]
    points += [(x, y) for y in range(y0, y1 + 1, step) for x in (x0, x1)]
    if not points:
        return (255, 255, 255)
    samples = [pixel(x, y)[:3] for x, y in points]
    buckets: dict[tuple, list] = {}
    for c in samples:
        buckets.setdefault(tuple(v // 16 for v in c), []).append(c)
    dominant = max(buckets.values(), key=len)
    return tuple(round(sum(c[i] for c in dominant) / len(dominant)) for i in range(3))


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


# Les boîtes de caractères d'un texte à interligne serré débordent sur les lignes voisines
# (souvent de 10 à 20 %) ; or le caviardage supprime TOUT caractère dont la boîte touche la zone.
# Pour supprimer du texte natif, on ne caviarde donc que le cœur vertical de la zone : il touche
# toujours les caractères visés, jamais ceux des lignes du dessus et du dessous.
CORE_VERTICAL_SHRINK = 0.25


def _core(rect: pymupdf.Rect) -> pymupdf.Rect:
    inset = rect.height * CORE_VERTICAL_SHRINK
    return pymupdf.Rect(rect.x0, rect.y0 + inset, rect.x1, rect.y1 - inset)


def _redact_native(page: pymupdf.Page, items: list[tuple[Line, Span]]) -> None:
    if not items:
        return
    text_boxes: list[tuple] = []
    image_boxes: list[tuple] = []
    for line, span in items:
        for rect in span_rects(page, line, span):
            # Couleur du fond, style du texte et place disponible mesurés AVANT toute modification.
            substitution = span.surrogate is not None
            fill = _page_background(page, rect) if substitution else FILL_RGB
            style = text_style(page, rect) if substitution and line.source == "native" else None
            width = available_width(line, span, rect, page.rect.width) if substitution else None
            (text_boxes if line.source == "native" else image_boxes).append((rect, span, fill, style, width))

    # 1. Texte natif : suppression réelle des caractères, zone resserrée verticalement.
    for rect, _, fill, _, _ in text_boxes:
        page.add_redact_annot(_core(rect), fill=fill, cross_out=False)
    if text_boxes:
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_NONE,
            graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
            text=pymupdf.PDF_REDACT_TEXT_REMOVE,
        )
    # 2. Texte lu par OCR dans une image (logo, tampon…) : effacement des pixels sur toute la
    #    zone, et des tracés vectoriels entièrement couverts ; le texte natif voisin est conservé.
    for rect, _, fill, _, _ in image_boxes:
        page.add_redact_annot(rect, fill=fill, cross_out=False)
    if image_boxes:
        page.apply_redactions(
            images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
            graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
            text=pymupdf.PDF_REDACT_TEXT_NONE,
        )
    for rect, span, _, style, width in text_boxes:
        write_replacement(page, rect if span.surrogate is not None else _core(rect), span, style, width)
    for rect, span, _, style, width in image_boxes:
        write_replacement(page, rect, span, style, width)


def _page_background(page: pymupdf.Page, rect: pymupdf.Rect) -> tuple[float, float, float]:
    """Couleur du fond autour de la zone, mesurée sur un rendu de la page (avant caviardage)."""
    clip = (pymupdf.Rect(rect) + (-3, -3, 3, 3)) & page.rect
    pix = page.get_pixmap(clip=clip, dpi=144, alpha=False)
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    r, g, b = ring_color(pix.pixel, 0, 0, pix.width - 1, pix.height - 1, pix.width, pix.height)
    return (r / 255, g / 255, b / 255)


def _rebuild_scanned(out: pymupdf.Document, page: pymupdf.Page, items: list[tuple[Line, Span]], dpi: int) -> None:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    if pix.colorspace is None or pix.colorspace.n != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    scale = dpi / 72.0
    gray = tuple(int(c * 255) for c in FILL_RGB)
    boxes: list[tuple] = []
    for line, span in items:
        for rect in span_rects(None, line, span):
            width = available_width(line, span, rect, page.rect.width) if span.surrogate is not None else None
            irect = pymupdf.IRect(
                max(0, int(rect.x0 * scale)),
                max(0, int(rect.y0 * scale)),
                min(pix.width, int(rect.x1 * scale) + 1),
                min(pix.height, int(rect.y1 * scale) + 1),
            )
            if not irect.is_empty:
                fill = (
                    ring_color(pix.pixel, irect.x0, irect.y0, irect.x1 - 1, irect.y1 - 1, pix.width, pix.height)
                    if span.surrogate is not None
                    else gray
                )
                pix.set_rect(irect, fill)
            boxes.append((rect, span, width))
    new_page = out.new_page(width=page.rect.width, height=page.rect.height)
    new_page.insert_image(new_page.rect, pixmap=pix)
    for rect, span, width in boxes:
        write_replacement(new_page, rect, span, max_width=width)
