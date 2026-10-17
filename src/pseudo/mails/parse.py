"""Lecture d'un fichier .eml : en-têtes utiles, corps (HTML de préférence, sinon texte) et pièces jointes.

Seule la bibliothèque standard `email` est utilisée. Rien n'est exécuté ni téléchargé : les pièces
jointes sont de simples octets, le format d'une pièce est déduit de son nom de fichier (ou, pour une
image intégrée sans nom, de son type MIME), comme pour les fichiers déposés.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser

from ..formats import EXTENSION_FOR_CONTENT_TYPE, format_from_name


class MailParseError(ValueError):
    """Le fichier n'est pas un mail lisible."""


@dataclass
class Address:
    """Une adresse d'en-tête : nom affiché (peut être vide) et adresse e-mail."""
    name: str
    email: str


@dataclass
class Attachment:
    """Pièce jointe, ou image intégrée au corps HTML (content_id renseigné)."""
    index: int
    filename: str  # nom d'origine (ou nom généré pour une image intégrée sans nom)
    fmt: str | None  # format déduit du nom ; None = non pris en charge
    data: bytes
    content_id: str | None = None  # référence « cid: » dans le corps HTML
    content_type: str = ""  # type MIME déclaré (image/gif, application/octet-stream…)


@dataclass
class ParsedMail:
    """Contenu utile d'un mail."""
    message_id: str
    date: datetime | None
    subject: str
    senders: list[Address] = field(default_factory=list)  # From
    recipients: dict[str, list[Address]] = field(default_factory=dict)  # To, Cc, Reply-To
    body: str = ""
    body_format: str = "txt"  # "html" | "txt"
    attachments: list[Attachment] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)  # parties refusées (ex. mail transféré en pièce jointe)


RECIPIENT_HEADERS = {"to": "À", "cc": "Cc", "reply-to": "Répondre à"}


def parse_eml(data: bytes) -> ParsedMail:
    """Découpe un .eml. Lève MailParseError si ce n'est pas un mail."""
    try:
        msg = BytesParser(policy=policy.default).parsebytes(data)
    except Exception as exc:  # noqa: BLE001 - tout contenu illisible est refusé
        raise MailParseError(str(exc)) from exc
    if not isinstance(msg, EmailMessage) or not (msg.get("from") or msg.get("to") or msg.get("subject")):
        raise MailParseError("en-têtes de mail absents")

    body_part = msg.get_body(preferencelist=("html", "plain"))
    mail = ParsedMail(
        message_id=str(msg.get("message-id") or "").strip(),
        date=_date(msg),
        subject=str(msg.get("subject") or ""),
        senders=_addresses(msg, "from"),
        recipients={key: _addresses(msg, key) for key in RECIPIENT_HEADERS if msg.get(key)},
    )
    if body_part is not None:
        mail.body = body_part.get_content()
        mail.body_format = "html" if body_part.get_content_type() == "text/html" else "txt"

    alternatives = {id(p) for p in _body_alternatives(msg)}
    for part in _leaves(msg):
        if part is body_part or id(part) in alternatives:
            continue
        if part.get_content_type() == "message/rfc822":
            mail.unsupported.append(part.get_filename() or "mail-joint.eml")
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        index = len(mail.attachments) + 1
        filename = part.get_filename() or _generated_name(index, part.get_content_type())
        content_id = (part.get("content-id") or "").strip().strip("<>") or None
        mail.attachments.append(Attachment(
            index, posixpath.basename(filename), format_from_name(filename), payload, content_id, part.get_content_type()
        ))
    return mail


def _leaves(part: EmailMessage):
    """Parties terminales du mail ; un mail joint (message/rfc822) est rendu tel quel, sans y descendre."""
    if part.get_content_type() == "message/rfc822":
        yield part
    elif part.is_multipart():
        for sub in part.iter_parts():
            yield from _leaves(sub)
    else:
        yield part


def _body_alternatives(msg: EmailMessage) -> list[EmailMessage]:
    """Versions du corps (texte et HTML) qui ne sont pas des pièces jointes : seule la préférée est gardée."""
    out = []
    for part in _leaves(msg):
        if part.get_content_type() in ("text/plain", "text/html") and not part.get_filename() and not part.is_attachment():
            out.append(part)
    return out


def _addresses(msg: EmailMessage, header: str) -> list[Address]:
    """Adresses d'un en-tête (From, To…), noms affichés décodés."""
    value = msg.get(header)
    if value is None:
        return []
    try:
        return [Address(a.display_name or "", a.addr_spec or "") for a in value.addresses]
    except Exception:  # noqa: BLE001 - en-tête mal formé : gardé comme texte brut
        return [Address("", str(value))]


def _date(msg: EmailMessage) -> datetime | None:
    """Date du mail (en-tête Date), ou None si absente ou illisible."""
    header = msg.get("date")
    try:
        return header.datetime if header is not None else None
    except Exception:  # noqa: BLE001
        return None


def _generated_name(index: int, content_type: str) -> str:
    """Nom d'une pièce sans nom de fichier : piece-<n> + extension du type MIME (.bin si inconnu)."""
    return f"piece-{index}{EXTENSION_FOR_CONTENT_TYPE.get(content_type, '.bin')}"
