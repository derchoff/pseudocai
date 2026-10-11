"""Entrée « image préparée » de l'étape de pseudonymisation (Function principale).

L'étape de préparation (app images) a déposé dans work/<job_id>/ :
- render.pdf     : l'image d'origine (cadrage conservé, orientation EXIF appliquée), une page par image ;
- ocr.pdf        : la copie redressée (perspective, inclinaison) et améliorée, destinée à l'OCR ;
- transforms.json: pour chaque page, dimensions et matrice « original -> copie OCR ».

Ici :
- TransformedOcr lit la copie OCR puis ramène la position de chaque mot sur l'image d'origine
  (matrice inverse) : les mots deviennent des quadrilatères qui épousent le texte vu de biais ;
- ImageRenderer remplace ces zones directement sur l'image d'origine (voir raster.py) et
  l'encode dans son format d'origine, sans EXIF. Aucune dépendance OpenCV.
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
from .raster import paint_spans, rect_quad


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
                    w.quad = apply_quad(to_render, w.quad or rect_quad(w.rect))
                    w.rect = bbox(w.quad)
        return results


class ImageRenderer:
    """Masque les zones sur l'image d'origine et l'encode dans son format d'origine, sans EXIF."""

    def __init__(self, original: bytes, fmt: str, pages: list[PageGeometry], settings: Settings):
        self._original = original
        self._fmt = fmt
        self._pages = pages
        self._settings = settings

    def __call__(self, pdf_bytes: bytes, content: DocumentContent, spans: list[Span], dpi: int) -> bytes:
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
            paint_spans(frame, items, g.dpi / 72)
        return encode(frames, self._fmt, self._settings.image_output_quality)


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

