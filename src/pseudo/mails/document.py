"""Texte d'un mail soumis au pipeline, puis relecture des valeurs pseudonymisées.

En-têtes (expéditeur, destinataires), objet, noms des pièces jointes et corps forment UN seul texte :
un seul passage dans les agents IA, et une même personne reçoit la même valeur fictive partout.
Chaque en-tête est une ligne « libellé : valeur » (le libellé donne le contexte à l'agent) ; la
valeur pseudonymisée est relue derrière le libellé.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from ..models import DocumentContent, Line, PageContent, Span, Word
from ..spans import pseudonymise_document
from .html_body import HtmlBody
from .parse import RECIPIENT_HEADERS, Address, ParsedMail

_WORD_RE = re.compile(r"\S+")
_ADDRESS_RE = re.compile(r"^(.*?)\s*<([^<>]*)>\s*$")
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def text_line(line_id: str, text: str) -> Line:
    """Ligne de texte sans position sur une page (le texte d'origine est gardé tel quel)."""
    words = [Word(text=m.group(), rect=(0.0, 0.0, 0.0, 0.0), start=m.start(), end=m.end()) for m in _WORD_RE.finditer(text)]
    return Line(id=line_id, page=0, words=words, text=text, source="native")


@dataclass
class MailText:
    """Texte d'un mail prêt pour le pipeline, avec de quoi relire chaque valeur."""
    content: DocumentContent
    prefixes: dict[str, str] = field(default_factory=dict)  # id de ligne -> libellé « De : »
    headers: dict[str, list[str]] = field(default_factory=dict)  # en-tête -> ids de ligne
    subject: str | None = None  # id de ligne de l'objet
    filenames: dict[int, str] = field(default_factory=dict)  # n° de pièce -> id de ligne du nom
    body_lines: list[tuple[str | None, str]] = field(default_factory=list)  # corps texte : (id, texte d'origine)
    html_keys: dict[str, tuple] = field(default_factory=dict)  # id de ligne -> clé du fragment HTML


def build_mail_text(mail: ParsedMail, html: HtmlBody | None) -> MailText:
    """Construit le texte analysé : en-têtes, objet, noms des pièces jointes, puis corps."""
    lines: list[Line] = []
    built = MailText(content=DocumentContent(pages=[]))

    def add(text: str, prefix: str = "") -> str:
        """Ajoute une ligne (libellé + texte) ; renvoie son identifiant."""
        line_id = f"p1-l{len(lines) + 1}"
        lines.append(text_line(line_id, prefix + text))
        if prefix:
            built.prefixes[line_id] = prefix
        return line_id

    for header, label, addresses in [("from", "De", mail.senders)] + [
        (key, RECIPIENT_HEADERS[key], mail.recipients.get(key, [])) for key in RECIPIENT_HEADERS
    ]:
        built.headers[header] = [add(_format_address(a), f"{label} : ") for a in addresses]
    if mail.subject.strip():
        built.subject = add(mail.subject, "Objet : ")
    for attachment in mail.attachments:
        stem, _ = posixpath.splitext(attachment.filename)
        built.filenames[attachment.index] = add(stem, "Pièce jointe (nom du fichier) : ")
    if html is not None:
        for key, text in html.fragments():
            built.html_keys[add(text)] = key
    else:
        for segment in mail.body.split("\n"):
            built.body_lines.append((add(segment) if segment.strip() else None, segment))
    built.content = DocumentContent(pages=[PageContent(index=0, source="native", lines=lines)])
    return built


@dataclass
class PseudonymisedMail:
    """Valeurs pseudonymisées relues après le pipeline."""
    headers: dict[str, list[dict]]  # en-tête -> [{"nom", "email"}]
    subject: str
    filenames: dict[int, str]  # n° de pièce -> nom de fichier final (extension d'origine)
    body: str  # corps reconstruit (HTML ou texte)
    body_text: str = ""  # corps en texte brut, sans balises (body.pseudonymise.txt)


def read_back(
    built: MailText, mail: ParsedMail, spans: list[Span], html: HtmlBody | None, cid_files: dict[str, str] | None = None
) -> PseudonymisedMail:
    """Relit les valeurs pseudonymisées (valeurs fictives ou jetons selon le mode de sortie)."""
    pseudo = {pl.id: pl.text for pl in pseudonymise_document(built.content, spans)}

    def value(line_id: str) -> str:
        """Valeur pseudonymisée d'une ligne, sans son libellé."""
        text = pseudo.get(line_id, "")
        prefix = built.prefixes.get(line_id, "")
        return text[len(prefix):] if text.startswith(prefix) else text

    headers = {name: [_parse_address(value(i)) for i in ids] for name, ids in built.headers.items() if ids}
    subject = value(built.subject) if built.subject else mail.subject
    filenames = final_filenames(mail, {n: value(i) for n, i in built.filenames.items()})
    if html is not None:
        cids = cid_files if cid_files is not None else {
            a.content_id: filenames[a.index] for a in mail.attachments if a.content_id
        }
        body = html.render({key: pseudo[i] for i, key in built.html_keys.items()}, cids)
        # Texte brut : les fragments de texte du HTML, dans l'ordre, un par ligne.
        body_text = "\n".join(pseudo[i] for i in built.html_keys)
    else:
        body = "\n".join(pseudo[i] if i else text for i, text in built.body_lines)
        body_text = body
    return PseudonymisedMail(headers=headers, subject=subject, filenames=filenames, body=body, body_text=body_text)


def final_filenames(mail: ParsedMail, stems: dict[int, str]) -> dict[int, str]:
    """Noms de fichier pseudonymisés, sûrs et uniques dans le dossier (extension d'origine conservée)."""
    used = {"mail.json", "rapport.json", "body.html", "body.txt"}
    out = {}
    for attachment in mail.attachments:
        _, ext = posixpath.splitext(attachment.filename)
        stem = _UNSAFE_FILENAME_RE.sub("_", stems.get(attachment.index, "")).strip(" .") or f"piece-{attachment.index}"
        name, k = f"{stem}{ext.lower()}", 2
        while name.lower() in used:
            name, k = f"{stem}-{k}{ext.lower()}", k + 1
        used.add(name.lower())
        out[attachment.index] = name
    return out


def _format_address(address: Address) -> str:
    """« Nom <adresse> », ou l'adresse seule."""
    return f"{address.name} <{address.email}>" if address.name else address.email


def _parse_address(text: str) -> dict:
    """Relit « Nom <adresse> » (ou une adresse seule) après pseudonymisation."""
    match = _ADDRESS_RE.match(text)
    if match:
        return {"nom": match.group(1).strip().strip('"'), "email": match.group(2).strip()}
    return {"nom": "", "email": text.strip()}
