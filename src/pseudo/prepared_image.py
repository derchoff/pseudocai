"""Entrée « image préparée » de l'étape de pseudonymisation (Function principale).

L'étape de préparation (app images) a déposé dans work/<job_id>/ :
- render.pdf     : l'image d'origine (cadrage conservé, orientation EXIF appliquée), une page par image ;
- ocr.pdf        : la copie redressée (perspective, inclinaison) et améliorée, destinée à l'OCR ;
- transforms.json: pour chaque page, dimensions et matrice « original -> copie OCR ».

Ici :
- TransformedOcr lit la copie OCR puis ramène la position de chaque mot sur l'image d'origine
  (matrice inverse) : les mots deviennent des quadrilatères qui épousent le texte vu de biais ;
- ImageRenderer masque ces quadrilatères directement sur l'image d'origine et l'encode dans son
  format d'origine, sans EXIF. Aucune dépendance OpenCV.
"""

from __future__ import annotations

from collections import defaultdict

import pymupdf

from .config import Settings
from .extraction import OcrEngine
from .geometry import PageGeometry, apply_quad, bbox, invert, load_geometry, multiply, scale
from .image_codec import decode, encode
from .jobs import Job
from .models import DocumentContent, Line, PageContent, Span
from .pipeline import PipelineInput
from .redaction import ring_color

FILL_RGB = (217, 217, 217)
TEXT_RGB = (0, 0, 0)
MARGIN_PX = 2.0


def _rect_quad(rect) -> tuple[float, ...]:
    x0, y0, x1, y1 = rect
    return (x0, y0, x1, y0, x1, y1, x0, y1)


class TransformedOcr:
    """OCR de la copie redressée ; positions exprimées dans les pages de render.pdf (points)."""

    def __init__(self, inner: OcrEngine, ocr_pdf: bytes, pages: list[PageGeometry]):
        self._inner = inner
        self._ocr_pdf = ocr_pdf
        self._pages = pages

    def ocr(self, pdf_bytes: bytes, doc: pymupdf.Document, page_indexes: list[int]) -> list[PageContent]:
        ocr_doc = pymupdf.open(stream=self._ocr_pdf, filetype="pdf")
        results = self._inner.ocr(self._ocr_pdf, ocr_doc, page_indexes)
        for page in results:
            g = self._pages[page.index]
            # points copie OCR -> pixels copie -> pixels original -> points de render.pdf
            to_render = multiply(scale(72 / g.dpi), multiply(invert(g.to_ocr), scale(g.dpi / 72)))
            for line in page.lines:
                for w in line.words:
                    w.quad = apply_quad(to_render, w.quad or _rect_quad(w.rect))
                    w.rect = bbox(w.quad)
        return results


def span_quads(line: Line, span: Span) -> list[tuple[float, ...]]:
    """Quadrilatères des mots couverts par la zone (un mot partiellement couvert est masqué en entier)."""
    return [w.quad or _rect_quad(w.rect) for w in line.words if w.start < span.end and w.end > span.start]


class ImageRenderer:
    """Masque les zones sur l'image d'origine et l'encode dans son format d'origine, sans EXIF."""

    def __init__(self, original: bytes, fmt: str, pages: list[PageGeometry], settings: Settings):
        self._original = original
        self._fmt = fmt
        self._pages = pages
        self._settings = settings

    def __call__(self, pdf_bytes: bytes, content: DocumentContent, spans: list[Span], dpi: int) -> bytes:
        from PIL import ImageDraw

        frames = decode(self._original, self._fmt, self._settings.image_max_pixels).frames
        by_page: dict[int, list[tuple[Line, Span]]] = defaultdict(list)
        for s in spans:
            line = content.line(s.line_id)
            if line is not None:
                by_page[line.page].append((line, s))
        for index, items in by_page.items():
            frame, g = frames[index], self._pages[index]
            if frame.size != (g.width, g.height):
                raise ValueError("L'image d'origine ne correspond pas à l'image préparée")
            draw = ImageDraw.Draw(frame)
            for line, span in items:
                quads_px = [[v * g.dpi / 72 for v in quad] for quad in span_quads(line, span)]
                if not quads_px:
                    continue
                boxes = [bbox(px) for px in quads_px]
                union = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                         max(b[2] for b in boxes), max(b[3] for b in boxes))
                if span.surrogate is not None:
                    # Mode substitution : zone de la couleur du fond, valeur fictive en texte ordinaire.
                    fill = ring_color(lambda x, y: frame.getpixel((x, y)), *(int(v) for v in union),
                                      frame.width, frame.height)
                else:
                    fill = FILL_RGB
                for px in quads_px:
                    draw.polygon(_expand(px, MARGIN_PX), fill=fill)
                # Un seul remplacement par zone, même si la valeur couvre plusieurs mots.
                if span.surrogate is not None:
                    _draw_surrogate(draw, span.surrogate, union)
                else:
                    _draw_token(draw, span.token, union)
        return encode(frames, self._fmt, self._settings.image_output_quality)


def _expand(px: list[float], margin: float) -> list[tuple[float, float]]:
    cx = sum(px[0::2]) / 4
    cy = sum(px[1::2]) / 4
    points = []
    for x, y in zip(px[0::2], px[1::2]):
        dx, dy = x - cx, y - cy
        norm = max((dx * dx + dy * dy) ** 0.5, 1e-6)
        points.append((x + dx / norm * margin, y + dy / norm * margin))
    return points


def _draw_token(draw, token: str, box: tuple[float, float, float, float]) -> None:
    from PIL import ImageFont

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    size = max(6, int(height * 0.7))
    font = _font(ImageFont, size)
    text_width = draw.textlength(token, font=font)
    if text_width > width * 0.95 and text_width > 0:
        size = max(6, int(size * width * 0.95 / text_width))
        font = _font(ImageFont, size)
        text_width = draw.textlength(token, font=font)
    draw.text((x0 + (width - text_width) / 2, y0 + (height - size) / 2), token, fill=TEXT_RGB, font=font)


def _draw_surrogate(draw, text: str, box: tuple[float, float, float, float]) -> None:
    """Valeur fictive : texte noir aligné à gauche, hauteur proche de celle de l'original."""
    from PIL import ImageFont

    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    size = max(6, int(height * 0.85))
    font = _font(ImageFont, size)
    text_width = draw.textlength(text, font=font)
    if text_width > width and text_width > 0:
        size = max(6, int(size * width / text_width))
        font = _font(ImageFont, size)
    draw.text((x0, y0 + (height - size) / 2), text, fill=TEXT_RGB, font=font)


def _font(image_font, size: int):
    try:
        return image_font.load_default(size=size)
    except TypeError:  # Pillow < 10.1 : police bitmap de taille fixe
        return image_font.load_default()


def load_image_input(job: Job, original: bytes, work_client, ocr: OcrEngine | None, settings: Settings) -> PipelineInput:
    """Construit l'entrée du pipeline pour une image préparée (work_client : container work/)."""
    prepared = job.prepared
    if prepared is None:
        raise ValueError("Job image sans fichiers préparés")

    def read(path: str) -> bytes:
        return work_client.get_blob_client(path).download_blob().readall()

    pages = load_geometry(read(prepared.transforms))
    render_pdf = read(prepared.render_pdf)
    if prepared.ocr_pdf and ocr is not None:
        ocr = TransformedOcr(ocr, read(prepared.ocr_pdf), pages)
    return PipelineInput(
        pdf=render_pdf,
        ocr=ocr,
        force_ocr=True,
        renderer=ImageRenderer(original, job.source_format, pages, settings),
        source={"origine": job.origin, "format": job.source_format, "preparation": prepared.preparation},
    )

