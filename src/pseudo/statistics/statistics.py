"""Fiches de statistiques (container Cosmos DB « statistiques ») : une par TRAITEMENT.

Chaque document terminé (publié ou en quarantaine) écrit une fiche, et un mail en écrit une pour
lui-même plus une par pièce jointe. Les compteurs (pages par période, % de quarantaine, types MIME,
tours de vérification, coût par page) sont calculés par requête sur ces fiches
(scripts/statistiques.py, docs/statistiques.md).

    {"id": "<job>", "jour": "2026-10-18", "year": 2026, "month": 10, "day": 18, "hour": 14,
     "niveau": "document", "categorie": "pdf", "mime": "application/pdf", "statut": "ok",
     "pages": 3, "toursVerification": 1, "residus": false, "consommation": {…}, …}

- Période = date et heure de DÉPOSE dans incoming/ (fuseau DEPOSIT_TIMEZONE), comme les dossiers
  year=/month=/day=/hour= ; « jour » est la clé de partition.
- Identifiant = job : un message relivré réécrit la même fiche ; un document retraité ou redéposé
  (nouveau job) en écrit une nouvelle, car il consomme à nouveau OCR et agents IA.
- Chaque fiche porte SA consommation : la fiche d'un mail celle de son texte et de son classement,
  chaque pièce la sienne. Aucune donnée personnelle : ni nom de fichier, ni valeur.
"""

from __future__ import annotations

import logging
import mimetypes
import posixpath
from datetime import datetime, timezone

from ..classification import Classification
from ..formats import CONTENT_TYPES, PDF, is_image
from ..jobs import ORIGIN_IMAGE, ORIGIN_JSON, ORIGIN_MAIL, ORIGIN_PDF, Job
from .usage import merge

log = logging.getLogger(__name__)

# Catégories : documents déposés, pièces de mails, annexes.
CATEGORY_PDF = "pdf"  # PDF hors mail
CATEGORY_IMAGE = "image"  # image hors mail
CATEGORY_MAIL = "mail"  # mail .eml
CATEGORY_OTHER = "autre"  # fichier refusé au routage (format non pris en charge)
CATEGORY_JSON = "json"  # JSON de métadonnées de la GED
CATEGORY_PDF_MAIL = "pdf_mail"  # PDF contenu dans un mail
CATEGORY_IMAGE_MAIL = "image_mail"  # image contenue dans un mail
CATEGORY_OTHER_MAIL = "autre_mail"  # autre pièce d'un mail (Word, ZIP, mail joint…)

LEVEL_DOCUMENT = "document"  # document déposé (un mail compte pour un)
LEVEL_PART = "piece"  # pièce jointe d'un mail
LEVEL_ANNEX = "annexe"  # JSON de métadonnées : pas un document

# Statuts d'une pièce de mail, en plus de ok / quarantine.
STATUS_REFUSED = "refusee"  # pièce non prise en charge : le mail entier part en quarantaine
STATUS_REMOVED = "retiree"  # image inutile retirée du mail (GIF, logo, icône)
STATUS_NOT_PROCESSED = "non_traitee"  # pièce acceptée, mais mail mis en quarantaine avant son traitement

RESIDUES_REASON = "residus_apres_verification"

# Table des types MIME de Python, sans celle du système (le registre Windows donne d'autres noms).
_MIME_TYPES = mimetypes.MimeTypes()


def record_statistics(ctx, job: Job, status: str, report: dict, classification: Classification) -> None:
    """Écrit les fiches d'un traitement terminé. Une erreur est journalisée, jamais propagée : les
    statistiques ne bloquent pas un document déjà publié."""
    try:
        items = build_items(job, status, report, classification, ctx.settings.deposit_timezone)
        ctx.mapping_store.save_statistics(items)
    except Exception:  # noqa: BLE001
        log.warning("Fiches de statistiques non écrites doc=%s job=%s", job.document_id, job.job_id, exc_info=True)


def build_items(job: Job, status: str, report: dict, classification: Classification | None, tz: str) -> list[dict]:
    """Fiches d'un traitement : une pour un document seul ou un JSON, une par mail plus une par pièce."""
    domain = (classification.dossier or None) if classification else None
    if job.origin == ORIGIN_MAIL:
        return _mail_items(job, status, report, domain, tz)
    category = {ORIGIN_PDF: CATEGORY_PDF, ORIGIN_IMAGE: CATEGORY_IMAGE, ORIGIN_JSON: CATEGORY_JSON}.get(job.origin, CATEGORY_OTHER)
    level = LEVEL_ANNEX if job.origin == ORIGIN_JSON else LEVEL_DOCUMENT
    return [_item(job, job.job_id, level, category, _mime(job), status, report, domain, tz)]


def _mail_items(job: Job, status: str, report: dict, domain: str | None, tz: str) -> list[dict]:
    """Fiche du mail (son texte et son classement) et fiches de ses pièces jointes."""
    corps = report.get("corps") or {}
    mail_report = {
        **corps,
        "motifs": report.get("motifs", []),
        "consommation": merge(corps.get("consommation"), report.get("consommation")),
        "pages": 0,  # les pages sont comptées dans les fiches des pièces
    }
    items = [_item(job, job.job_id, LEVEL_DOCUMENT, CATEGORY_MAIL, CONTENT_TYPES.get("eml", "message/rfc822"),
                   status, mail_report, domain, tz)]
    # Pièces traitées (finalisation du mail).
    for piece in report.get("pieces_jointes") or []:
        category = CATEGORY_PDF_MAIL if piece.get("format") == PDF else CATEGORY_IMAGE_MAIL
        items.append(_part(job, piece, category, piece.get("statut", status), piece.get("rapport") or {}, domain, tz))
    # Mail mis en quarantaine au découpage : pièces refusées et pièces acceptées non traitées.
    for piece in report.get("pieces_refusees") or []:
        if piece.get("piece") is None and piece.get("motif") != "mail_joint":
            continue  # ligne de résumé (trop de pièces jointes)
        image = (piece.get("mime") or "").startswith("image/")
        category = CATEGORY_IMAGE_MAIL if image else CATEGORY_OTHER_MAIL
        items.append(_part(job, piece, category, STATUS_REFUSED, {"motifs": [piece.get("motif")]}, domain, tz))
    for piece in report.get("pieces_non_traitees") or []:
        category = CATEGORY_PDF_MAIL if piece.get("format") == PDF else CATEGORY_IMAGE_MAIL
        items.append(_part(job, piece, category, STATUS_NOT_PROCESSED, {}, domain, tz))
    # Images retirées du mail (GIF, logos, icônes), dans les deux cas.
    for piece in report.get("pieces_retirees") or []:
        items.append(_part(job, piece, CATEGORY_IMAGE_MAIL, STATUS_REMOVED, {"motifs": [piece.get("motif")]}, domain, tz))
    return items


def _part(job: Job, piece: dict, category: str, status: str, report: dict, domain: str | None, tz: str) -> dict:
    """Fiche d'une pièce jointe ; identifiant : job du mail + numéro de la pièce."""
    index = piece.get("index", piece.get("piece"))
    item_id = f"{job.job_id}-p{index}" if index is not None else f"{job.job_id}-p-{piece.get('motif', 'x')}"
    mime = piece.get("mime") or _mime_of_format(piece.get("format"))
    return {
        **_item(job, item_id, LEVEL_PART, category, mime, status, report, domain, tz),
        "mailJobId": job.job_id,
        "documentId": f"{job.document_id}-{index:02d}" if isinstance(index, int) else job.document_id,
    }


def _item(job: Job, item_id: str, level: str, category: str, mime: str, status: str, report: dict,
          domain: str | None, tz: str) -> dict:
    """Fiche commune : période de dépose, catégorie, statut, pages, tours, consommation."""
    local = job.deposited_local(tz)
    motifs = [m for m in (report.get("motifs") or []) if m]
    return {
        "id": item_id,
        "jour": f"{local:%Y-%m-%d}",
        "year": local.year,
        "month": local.month,
        "day": local.day,
        "hour": local.hour,
        "niveau": level,
        "categorie": category,
        "mime": mime,
        "extension": posixpath.splitext(job.source_name)[1].lower() if level != LEVEL_PART else None,
        "documentId": job.document_id,
        "jobId": job.job_id,
        "mailJobId": None,
        "statut": status,
        "motifs": motifs,
        "domain": domain,
        "pages": _pages(report),
        "toursVerification": int(report.get("tours_verification", 0) or 0),
        "residus": RESIDUES_REASON in motifs,
        "consommation": merge(report.get("consommation")),
        "deposeLe": job.deposited_at or job.received_at,
        "traiteLe": datetime.now(timezone.utc).isoformat(),
    }


def _pages(report: dict) -> int:
    """Pages du document : celles lues par le pipeline, sinon celles de la préparation d'une image."""
    pages = report.get("pages")
    if pages is None:
        pages = (report.get("preparation") or {}).get("pages")
    return int(pages or 0)


def _mime(job: Job) -> str:
    """Type MIME d'un document déposé : d'après son format, sinon d'après son extension."""
    return CONTENT_TYPES.get(job.source_format) or _MIME_TYPES.guess_type(job.source_name)[0] or "application/octet-stream"


def _mime_of_format(fmt: str | None) -> str:
    """Type MIME d'une pièce d'après son format reconnu."""
    if fmt and (fmt == PDF or is_image(fmt)):
        return CONTENT_TYPES[fmt]
    return "application/octet-stream"
