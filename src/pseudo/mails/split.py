"""Découpage d'un mail (file « mail-jobs », app principale). Aucun appel aux agents IA.

1. Lecture du .eml. Sont retirées du résultat, sans quarantaine : les GIF (illisibles par l'OCR),
   les images de moins de MAIL_IMAGE_MIN_SIDE pixels sur un côté, quels que soient leur format et leur
   type déclaré (logos, icônes de signature), et les images INTÉGRÉES au corps trop petites pour être
   lues (sous IMAGE_MIN_SHORT_SIDE). Pièce non prise en charge (Word, ZIP, mail joint…), image alors que les
   images sont désactivées, ou trop de pièces : le mail entier part en quarantaine.
2. Dossier de travail : état du mail et pièces d'origine (voir workspace.py).
3. Une tâche « texte du mail » dans « text-jobs » (en-têtes, objet, noms des pièces et corps,
   pseudonymisés ensemble : voir text.py) et une tâche par pièce jointe, dans « pdf-jobs » ou
   « image-jobs ». La finalisation publie le mail quand toutes sont terminées.

Le dossier publié porte le nom du fichier .eml d'origine, sans son extension, sous son chemin :
incoming/lot1/Acte de deces - 9253367.eml -> pseudonymised/<type>/lot1/Acte de deces - 9253367/.
"""

from __future__ import annotations

import logging
import posixpath
import uuid

from ..formats import PDF, is_image
from ..image_codec import image_size, is_gif, sniff_size
from ..jobs import ORIGIN_IMAGE, ORIGIN_MAIL_TEXT, ORIGIN_PDF, Job
from ..outputs import job_source_blob, previous_trace
from ..router import QUEUE_IMAGE, QUEUE_PDF, QUEUE_TEXT
from .finalize import TEXT_PART, quarantine_mail
from .parse import MailParseError, ParsedMail, parse_eml
from .workspace import MailWorkspace

log = logging.getLogger(__name__)


def handle_mail_job(body: bytes | str, runtime) -> list[tuple[str, str]]:
    """Découpe le mail du job ; renvoie ses tâches : [(file, job JSON)], texte du mail en premier."""
    settings = runtime.settings
    job = Job.from_json(body)
    previous = previous_trace(runtime, job)
    if previous and previous.get("jobId") == job.job_id:
        log.info("Mail déjà traité doc=%s job=%s", job.document_id, job.job_id)
        return []
    source = job_source_blob(runtime, job)
    if not source.exists():
        log.warning("Mail introuvable doc=%s", job.document_id)
        return []
    original = source.download_blob().readall()

    try:
        mail = parse_eml(original)
    except MailParseError:
        quarantine_mail(runtime, job, original, mail_folder(job), ["mail_illisible"], {}, None, previous)
        return []
    folder = mail_folder(job)
    removed = _drop_unusable_images(mail, settings)
    refused = _refusals(mail, settings)
    if refused:
        reasons, detail = refused
        quarantine_mail(runtime, job, original, folder, reasons, {"pieces_refusees": detail}, None, previous)
        return []

    ws = MailWorkspace.for_job(runtime, job.job_id)
    parts, children = [], []
    for attachment in mail.attachments:
        origin = ORIGIN_PDF if attachment.fmt == PDF else ORIGIN_IMAGE
        _, ext = posixpath.splitext(attachment.filename)
        source_name = ws.put(f"in/{attachment.index}{ext.lower()}", attachment.data)
        part_document_id = f"{job.document_id}-{attachment.index:02d}"
        parts.append({
            "index": attachment.index, "format": attachment.fmt,
            "inline": attachment.content_id is not None, "document_id": part_document_id,
        })
        child = Job(
            job_id=str(uuid.uuid4()), document_id=part_document_id, source_container=settings.work_container,
            source_name=source_name, source_format=attachment.fmt, origin=origin, received_at=job.received_at,
            parent={"mail": ws.prefix, "part": attachment.index},
        )
        children.append((QUEUE_PDF if origin == ORIGIN_PDF else QUEUE_IMAGE, child.to_json()))
    # État écrit AVANT l'envoi des tâches : chacune le relit.
    ws.put_json("state.json", {"job": job.to_json(), "folder": folder, "parts": parts, "images_retirees": removed})
    text_job = Job(
        job_id=str(uuid.uuid4()), document_id=f"{job.document_id}-texte", source_container=job.source_container,
        source_name=job.source_name, source_format=job.source_format, origin=ORIGIN_MAIL_TEXT,
        received_at=job.received_at, parent={"mail": ws.prefix, "part": TEXT_PART},
    )
    log.info("Mail découpé doc=%s pieces=%d", job.document_id, len(parts))
    return [(QUEUE_TEXT, text_job.to_json())] + children


def mail_folder(job: Job) -> str:
    """Nom du dossier du mail : son chemin dans incoming/, sans l'extension .eml
    (lot1/Acte de deces - 9253367.eml -> lot1/Acte de deces - 9253367). Stable : un retraitement
    remplace le dossier précédent."""
    return posixpath.splitext(job.source_name)[0]


def _drop_unusable_images(mail: ParsedMail, settings) -> dict[str, int]:
    """Retire les images d'un mail inutiles ou illisibles par l'OCR : elles ne sont ni traitées ni
    publiées, et leur référence disparaît du HTML. Renvoie leur nombre par raison :

    - gif : toute image GIF (nom, type MIME ou contenu), que l'OCR ne lit pas ;
    - trop_petites : toute image de moins de MAIL_IMAGE_MIN_SIDE pixels sur un côté, quels que soient
      son format et son type déclaré (une icône envoyée en application/octet-stream sans nom compris) ;
    - integrees_illisibles : image intégrée au corps sous IMAGE_MIN_SHORT_SIDE (logo, bandeau).
    """
    kept: list = []
    removed = {"gif": 0, "trop_petites": 0, "integrees_illisibles": 0}
    for a in mail.attachments:
        if a.filename.lower().endswith(".gif") or a.content_type == "image/gif" or is_gif(a.data):
            removed["gif"] += 1
            continue
        size = image_size(a.data, a.fmt) if is_image(a.fmt) else sniff_size(a.data)
        if size is not None and min(size) < settings.mail_image_min_side:
            removed["trop_petites"] += 1
            continue
        if size is not None and a.content_id and is_image(a.fmt) and min(size) < settings.image_min_short_side:
            removed["integrees_illisibles"] += 1
            continue
        kept.append(a)
    mail.attachments = kept
    return removed


def _refusals(mail: ParsedMail, settings) -> tuple[list[str], list[dict]] | None:
    """Motifs de quarantaine immédiate du mail entier, et détail (extensions seulement : un nom de
    fichier peut contenir un nom de personne)."""
    reasons, detail = [], []
    for name in mail.unsupported:
        reasons.append("piece_jointe_non_prise_en_charge")
        detail.append({"piece": None, "extension": posixpath.splitext(name)[1].lower(), "motif": "mail_joint"})
    for a in mail.attachments:
        ext = posixpath.splitext(a.filename)[1].lower()
        motif = None
        if a.fmt is None or (a.fmt != PDF and not is_image(a.fmt)):
            motif = "format_non_supporte"
        elif is_image(a.fmt) and (not settings.image_processing_enabled or ext not in settings.image_extensions):
            motif = "traitement_images_desactive"
        if motif:
            reasons.append("piece_jointe_non_prise_en_charge")
            detail.append({"piece": a.index, "extension": ext, "motif": motif})
    if len(mail.attachments) > settings.mail_max_attachments:
        reasons.append("trop_de_pieces_jointes")
        detail.append({"pieces": len(mail.attachments), "maximum": settings.mail_max_attachments})
    return (sorted(set(reasons)), detail) if reasons else None
