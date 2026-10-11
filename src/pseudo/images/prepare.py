"""Étape 2 — préparation d'une image : décodage, contrôles qualité, copie OCR, emballage en PDF.

    image (incoming/) ──► décodage (EXIF, HEIC, TIFF multipage)
                     ──► contrôles qualité ── échec ──► quarantine/non-classe/
                     ──► render.pdf (original, cadrage conservé) + ocr.pdf (copie redressée)
                         + transforms.json  ──► work/<job_id>/  ──► job « pdf-jobs »
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

import pymupdf

from ..classification import STATUT_NON_APPLICABLE, unclassified
from ..config import Settings
from ..geometry import IDENTITY, PageGeometry, dump_geometry
from ..image_codec import ImageDecodeError, ImageTooLargeError, decode
from ..jobs import Job, PreparedImage
from ..outputs import STAGE_IMAGE, StorageContext, previous_trace, quarantine
from .preprocess import prepare_ocr_copy
from .quality import check_quality

log = logging.getLogger(__name__)

RENDER_JPEG_QUALITY = 95


@dataclass
class PreparedArtifacts:
    render_pdf: bytes
    transforms: bytes
    ocr_pdf: bytes | None
    preparation: dict
    reasons: list[str] = field(default_factory=list)  # motifs de quarantaine (vide si OK)


def prepare_image(data: bytes, fmt: str, settings: Settings) -> PreparedArtifacts:
    """Fonction pure (sans stockage), utilisée par la Function images et par cli.py."""
    try:
        decoded = decode(data, fmt, settings.image_max_pixels)
    except ImageTooLargeError as exc:
        return PreparedArtifacts(b"", b"", None, {"dimensions": str(exc)}, ["image_trop_grande"])
    except ImageDecodeError:
        return PreparedArtifacts(b"", b"", None, {}, ["image_illisible"])

    reasons: list[str] = []
    pages_info = []
    geometry: list[PageGeometry] = []
    ocr_frames = []
    for frame in decoded.frames:
        frame_reasons, metrics = check_quality(
            frame, min_short_side=settings.image_min_short_side, blur_threshold=settings.image_blur_threshold
        )
        reasons += [r for r in frame_reasons if r not in reasons]
        matrix = IDENTITY
        if settings.image_enhance_for_ocr and not frame_reasons:
            ocr_frame, matrix, info = prepare_ocr_copy(frame, enhance=True)
            ocr_frames.append(ocr_frame)
            metrics.update(info)
        pages_info.append(metrics)
        geometry.append(PageGeometry(frame.width, frame.height, settings.image_render_dpi, matrix))

    preparation = {
        "format": fmt,
        "pages": len(decoded.frames),
        "orientation_exif": decoded.exif_orientation,
        "copie_ocr": settings.image_enhance_for_ocr,
        "pages_detail": pages_info,
    }
    if reasons:
        return PreparedArtifacts(b"", b"", None, preparation, reasons)
    return PreparedArtifacts(
        render_pdf=_wrap(decoded.frames, settings.image_render_dpi, "JPEG"),
        transforms=dump_geometry(geometry),
        ocr_pdf=_wrap(ocr_frames, settings.image_render_dpi, "PNG") if ocr_frames else None,
        preparation=preparation,
    )


def handle_image_job(body: bytes | str, ctx: StorageContext) -> Job | None:
    """Prépare l'image du job et renvoie le job à transmettre à « pdf-jobs » (None si quarantaine)."""
    settings = ctx.settings
    job = Job.from_json(body)
    blob = ctx.blob_service.get_blob_client(job.source_container, job.source_name)
    if not blob.exists():
        log.warning("Image introuvable dans incoming/ doc=%s", job.document_id)
        return None
    original = blob.download_blob().readall()
    artifacts = prepare_image(original, job.source_format, settings)

    if artifacts.reasons:
        quarantine(
            ctx,
            job,
            original,
            artifacts.reasons,
            {"preparation": artifacts.preparation},
            unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder),
            previous_trace(ctx, job),
            STAGE_IMAGE,
        )
        return None

    work = ctx.blob_service.get_container_client(settings.work_container)
    paths = {
        "render_pdf": f"{job.work_prefix}render.pdf",
        "transforms": f"{job.work_prefix}transforms.json",
        "ocr_pdf": f"{job.work_prefix}ocr.pdf" if artifacts.ocr_pdf else None,
    }
    work.upload_blob(paths["render_pdf"], artifacts.render_pdf, overwrite=True)
    work.upload_blob(paths["transforms"], artifacts.transforms, overwrite=True)
    if artifacts.ocr_pdf:
        work.upload_blob(paths["ocr_pdf"], artifacts.ocr_pdf, overwrite=True)
    job.prepared = PreparedImage(preparation=artifacts.preparation, **paths)
    log.info("Image préparée doc=%s job=%s pages=%d", job.document_id, job.job_id, artifacts.preparation["pages"])
    return job


def _wrap(frames, dpi: int, pil_format: str) -> bytes:
    """Emballe des images dans un PDF, une page par image (points = pixels × 72 / dpi)."""
    doc = pymupdf.open()
    for frame in frames:
        buffer = io.BytesIO()
        if pil_format == "JPEG":
            frame.save(buffer, format="JPEG", quality=RENDER_JPEG_QUALITY)
        else:
            frame.save(buffer, format=pil_format)
        page = doc.new_page(width=frame.width * 72 / dpi, height=frame.height * 72 / dpi)
        page.insert_image(page.rect, stream=buffer.getvalue())
    return doc.tobytes(deflate=True)
