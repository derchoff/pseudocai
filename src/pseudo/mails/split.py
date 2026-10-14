"""Découpage d'un mail (file « mail-jobs », app principale).

1. Lecture du .eml. Une image INTÉGRÉE au corps trop petite pour être lue (logo, icône de signature)
   est retirée du résultat. Pièce non prise en charge (Word, ZIP, mail joint…), image alors que les
   images sont désactivées, ou trop de pièces : le mail entier part en quarantaine, sans appel aux
   agents IA.
2. En-têtes, objet, noms des pièces et corps pseudonymisés en un seul passage (document.py).
3. Dossier de travail : mail.json, body.html|txt, pièces d'origine (voir workspace.py).
4. Une tâche par pièce jointe, dans « pdf-jobs » ou « image-jobs ». Sans pièce jointe, le mail est
   publié tout de suite.
"""

from __future__ import annotations

import logging
import posixpath
import uuid
from datetime import datetime

from ..formats import PDF, is_image
from ..image_codec import image_size
from ..jobs import ORIGIN_IMAGE, ORIGIN_PDF, Job
from ..outputs import job_source_blob, previous_trace
from ..pipeline import PipelineInput, run_pipeline
from ..router import QUEUE_IMAGE, QUEUE_PDF
from .document import build_mail_text, read_back
from .finalize import quarantine_mail, try_finalize
from .html_body import HtmlBody
from .parse import MailParseError, ParsedMail, parse_eml
from .workspace import MailWorkspace

log = logging.getLogger(__name__)


def handle_mail_job(body: bytes | str, runtime) -> list[tuple[str, str]]:
    """Découpe le mail du job ; renvoie les tâches des pièces jointes : [(file, job JSON)]."""
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
        quarantine_mail(runtime, job, original, _fallback_folder(job), ["mail_illisible"], {}, None, previous)
        return []
    folder = mail_folder(mail, job, runtime.services.pseudonymizer)
    removed = _drop_small_inline_images(mail, settings)
    refused = _refusals(mail, settings)
    if refused:
        reasons, detail = refused
        quarantine_mail(runtime, job, original, folder, reasons, {"pieces_refusees": detail}, None, previous)
        return []

    # En-têtes, objet, noms des pièces et corps : un seul passage dans le pipeline.
    html = HtmlBody(mail.body) if mail.body_format == "html" else None
    built = build_mail_text(mail, html)
    result = run_pipeline(
        PipelineInput(pdf=b"", content=built.content, classify=False, source={"origine": "mail", "format": "eml"}),
        settings,
        runtime.services,
    )
    runtime.mapping_store.save(result.records)  # avant toute publication : un jeton publié est réversible
    pseudo = read_back(built, mail, result.spans, html)

    ws = MailWorkspace.for_job(runtime, job.job_id)
    body_file = "body.html" if html is not None else "body.txt"
    ws.put("text/body.txt", result.token_text or "")
    ws.put(f"out/{body_file}", pseudo.body)
    ws.put_json("out/mail.json", _mail_json(mail, folder, pseudo, body_file))
    header_lines = set(built.prefixes)
    occurrences = [
        {**o, "fichier": "mail.json" if o["ligne"] in header_lines else body_file} for o in result.occurrences
    ]
    parts, children = [], []
    for attachment in mail.attachments:
        origin = ORIGIN_PDF if attachment.fmt == PDF else ORIGIN_IMAGE
        _, ext = posixpath.splitext(attachment.filename)
        source_name = ws.put(f"in/{attachment.index}{ext.lower()}", attachment.data)
        part_document_id = f"{job.document_id}-{attachment.index:02d}"
        parts.append({
            "index": attachment.index, "name": pseudo.filenames[attachment.index], "format": attachment.fmt,
            "inline": attachment.content_id is not None, "document_id": part_document_id,
        })
        child = Job(
            job_id=str(uuid.uuid4()), document_id=part_document_id, source_container=settings.work_container,
            source_name=source_name, source_format=attachment.fmt, origin=origin, received_at=job.received_at,
            parent={"mail": ws.prefix, "part": attachment.index, "name": pseudo.filenames[attachment.index]},
        )
        children.append((QUEUE_PDF if origin == ORIGIN_PDF else QUEUE_IMAGE, child.to_json()))
    ws.put_json("state.json", {
        "job": job.to_json(),
        "folder": folder,
        "parts": parts,
        "body": {
            "file": body_file,
            "content_type": "text/html; charset=utf-8" if html is not None else "text/plain; charset=utf-8",
            "reasons": result.reasons,
            "report": {**result.report, "images_integrees_retirees": removed},
            "occurrences": occurrences,
        },
    })
    log.info("Mail découpé doc=%s pieces=%d corps=%s", job.document_id, len(parts), body_file)
    if not parts:
        try_finalize(runtime, ws)
    return children


def mail_folder(mail: ParsedMail, job: Job, pseudonymizer) -> str:
    """Nom du dossier : mail-<date>-<empreinte>. Aucune donnée personnelle ; stable pour un même
    mail (même Message-ID), donc un retraitement remplace le dossier précédent."""
    date = mail.date or datetime.fromisoformat(job.received_at)
    key = mail.message_id or job.document_id
    return f"mail-{date:%Y%m%d}-{pseudonymizer.fingerprint('MAIL', key, 'texte')[:8]}"


def _drop_small_inline_images(mail: ParsedMail, settings) -> int:
    """Retire les images intégrées au corps trop petites pour être lues (logos, icônes) : elles ne
    sont pas publiées et leur référence disparaît du HTML. Renvoie leur nombre."""
    kept, removed = [], 0
    for a in mail.attachments:
        size = image_size(a.data, a.fmt) if a.content_id and is_image(a.fmt) else None
        if size is not None and min(size) < settings.image_min_short_side:
            removed += 1
            continue
        kept.append(a)
    mail.attachments = kept
    return removed


def _fallback_folder(job: Job) -> str:
    """Nom du dossier d'un fichier .eml illisible."""
    return f"mail-{datetime.fromisoformat(job.received_at):%Y%m%d}-{job.document_id[:8]}"


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


def _mail_json(mail: ParsedMail, folder: str, pseudo, body_file: str) -> dict:
    """Contenu de mail.json : en-têtes pseudonymisés (organisations de la liste conservées)."""
    return {
        "dossier": folder,
        "date": mail.date.isoformat() if mail.date else None,
        "objet": pseudo.subject,
        "de": pseudo.headers.get("from", []),
        "a": pseudo.headers.get("to", []),
        "cc": pseudo.headers.get("cc", []),
        "repondre_a": pseudo.headers.get("reply-to", []),
        "corps": body_file,
        "pieces_jointes": [
            {"fichier": pseudo.filenames[a.index], "format": a.fmt, "integree": a.content_id is not None}
            for a in mail.attachments
        ],
    }

