"""Étape 1 — routage : un fichier déposé dans incoming/ est reconnu et envoyé vers la bonne file.

    PDF                         -> job « pdf-jobs »   (pseudonymisation directe)
    image (si activé)           -> job « image-jobs » (préparation, puis « pdf-jobs »)
    image (traitement désactivé), format inconnu, fichier trop volumineux
                                -> quarantine/non-classe/

Le format est déduit uniquement de l'extension (voir formats.py) : un fichier dont le contenu ne
correspond pas à son extension échouera à la lecture, à l'étape suivante, et partira en quarantaine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .classification import STATUT_NON_APPLICABLE, unclassified
from .events import parse_blob_event
from .formats import PDF, extension, format_from_name, is_image
from .jobs import ORIGIN_IMAGE, ORIGIN_PDF, Job
from .outputs import STAGE_ROUTING, StorageContext, previous_trace, quarantine

log = logging.getLogger(__name__)

QUEUE_PDF = "pdf"
QUEUE_IMAGE = "image"


@dataclass
class RouteResult:
    status: str  # "routed" | "quarantine" | "ignored" | "missing"
    queue: str | None = None  # QUEUE_PDF | QUEUE_IMAGE
    job: Job | None = None


def route_event(body: bytes | str, ctx: StorageContext) -> RouteResult:
    settings = ctx.settings
    event = parse_blob_event(body)
    if event.container != settings.incoming_container:
        log.warning("Événement ignoré (container %s)", event.container)
        return RouteResult("ignored")

    blob = ctx.blob_service.get_blob_client(event.container, event.name)
    if not blob.exists():
        log.warning("Fichier introuvable dans incoming/ (supprimé entre-temps)")
        return RouteResult("missing")
    fmt = format_from_name(event.name)
    if is_image(fmt) and extension(event.name) not in settings.image_extensions:
        fmt = None  # extension d'image non retenue dans IMAGE_EXTENSIONS
    origin = ORIGIN_PDF if fmt == PDF else ORIGIN_IMAGE if is_image(fmt) else None
    job = Job.new(event.url, event.container, event.name, fmt or "inconnu", origin or "inconnu")

    reason = None
    if fmt is None:
        reason = "format_non_supporte"
    elif origin == ORIGIN_IMAGE and not settings.image_processing_enabled:
        reason = "traitement_images_desactive"
    elif blob.get_blob_properties().size > settings.max_file_mb * 1024 * 1024:
        reason = "fichier_trop_volumineux"
    if reason:
        quarantine(
            ctx,
            job,
            blob.download_blob().readall(),
            [reason],
            {"format": job.source_format, "extension": extension(event.name)},
            unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder),
            previous_trace(ctx, job),
            STAGE_ROUTING,
        )
        return RouteResult("quarantine", job=job)

    queue = QUEUE_PDF if origin == ORIGIN_PDF else QUEUE_IMAGE
    log.info("Document routé doc=%s format=%s file=%s job=%s", job.document_id, fmt, queue, job.job_id)
    return RouteResult("routed", queue=queue, job=job)
