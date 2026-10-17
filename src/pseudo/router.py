"""Étape 1 — routage : un fichier déposé dans incoming/ est reconnu et envoyé vers la bonne file.

    PDF                         -> job « pdf-jobs »   (pseudonymisation directe)
    image (si activé)           -> job « image-jobs » (préparation, puis « pdf-jobs »)
    mail .eml (si activé)       -> job « mail-jobs »  (découpage, pièces jointes, dossier par mail)
    image ou mail (traitement désactivé), format inconnu, fichier trop volumineux
                                -> quarantine/non-classe/

Le format est déduit uniquement de l'extension (voir formats.py) : un fichier dont le contenu ne
correspond pas à son extension échouera à la lecture, à l'étape suivante, et partira en quarantaine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from .classification import STATUT_NON_APPLICABLE, unclassified
from .events import parse_blob_event
from .formats import JSON, MAIL, PDF, extension, format_from_name, is_image
from .jobs import ORIGIN_IMAGE, ORIGIN_JSON, ORIGIN_MAIL, ORIGIN_PDF, Job, document_id
from .outputs import STAGE_ROUTING, StorageContext, previous_trace, quarantine, source_blob

log = logging.getLogger(__name__)

QUEUE_PDF = "pdf"
QUEUE_IMAGE = "image"
QUEUE_MAIL = "mail"
QUEUE_TEXT = "text"


@dataclass
class RouteResult:
    """Résultat du routage : statut, file de destination et job créé."""
    status: str  # "routed" | "quarantine" | "ignored" | "missing"
    queue: str | None = None  # QUEUE_PDF | QUEUE_IMAGE | QUEUE_MAIL | QUEUE_TEXT
    job: Job | None = None


def route_event(body: bytes | str, ctx: StorageContext) -> RouteResult:
    """Décide du sort d'un fichier déposé dans incoming/, d'après son extension uniquement.

    PDF -> file pdf-jobs, image -> file image-jobs, mail -> file mail-jobs, JSON de métadonnées
    (<nom du document>.json) -> file text-jobs. Format inconnu, images ou mails désactivés, fichier
    trop gros -> quarantaine. Un JSON alors que les JSON sont désactivés est ignoré (il n'est
    qu'une annexe de son document).
    """
    settings = ctx.settings
    event = parse_blob_event(body)
    if event.container != settings.incoming_container or not _same_account(event.url, settings.source_storage_account_url):
        log.warning("Événement ignoré (compte ou container inattendu : %s)", event.container)
        return RouteResult("ignored")

    blob = source_blob(ctx, event.container, event.name)
    if not blob.exists():
        log.warning("Fichier introuvable dans incoming/ (supprimé entre-temps)")
        return RouteResult("missing")
    fmt = format_from_name(event.name)
    if is_image(fmt) and extension(event.name) not in settings.image_extensions:
        fmt = None  # extension d'image non retenue dans IMAGE_EXTENSIONS
    if fmt == JSON and not settings.json_metadata_enabled:
        log.info("JSON de métadonnées ignoré (JSON_METADATA_ENABLED=false)")
        return RouteResult("ignored")
    origin = {PDF: ORIGIN_PDF, MAIL: ORIGIN_MAIL, JSON: ORIGIN_JSON}.get(fmt) or (ORIGIN_IMAGE if is_image(fmt) else None)
    job = Job.new(event.url, event.container, event.name, fmt or "inconnu", origin or "inconnu")
    if origin == ORIGIN_JSON:
        # Le JSON est rattaché à son document : même identifiant (chemin du document = sans .json).
        job.document_id = document_id(event.url[: -len(".json")])

    reason = None
    if fmt is None:
        reason = "format_non_supporte"
    elif origin == ORIGIN_IMAGE and not settings.image_processing_enabled:
        reason = "traitement_images_desactive"
    elif origin == ORIGIN_MAIL and not settings.mail_processing_enabled:
        reason = "traitement_mails_desactive"
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

    queue = {ORIGIN_PDF: QUEUE_PDF, ORIGIN_IMAGE: QUEUE_IMAGE, ORIGIN_MAIL: QUEUE_MAIL, ORIGIN_JSON: QUEUE_TEXT}[origin]
    log.info("Document routé doc=%s format=%s file=%s job=%s", job.document_id, fmt, queue, job.job_id)
    return RouteResult("routed", queue=queue, job=job)


def _same_account(event_url: str, account_url: str | None) -> bool:
    """Vrai si l'événement vient du compte d'origine configuré (toujours vrai sans URL configurée)."""
    if not account_url:
        return True
    return urlparse(event_url).hostname.lower() == (urlparse(account_url).hostname or "").lower()
