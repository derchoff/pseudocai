"""Affiche la structure MIME d'un ou plusieurs .eml, sans aucun contenu ni nom de fichier.

Pour chaque partie : type MIME, disposition (attachment / inline), extension du nom de fichier
(ou AUCUN), présence d'un Content-ID, taille et premiers octets reconnus (signature PDF, JPEG…).
Sert à comprendre pourquoi une pièce est refusée (« .bin » = pièce sans nom de fichier, de type
MIME inconnu).

    python scripts/inspecter-eml.py chemin/vers/mail.eml [autre.eml ...]
"""

import posixpath
import sys
from email import policy
from email.parser import BytesParser

SIGNATURES = {
    b"%PDF-": "pdf",
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG": "png",
    b"II*\x00": "tiff",
    b"MM\x00*": "tiff",
    b"PK\x03\x04": "zip/office",
    b"\xd0\xcf\x11\xe0": "office-ancien/msg",
    b"\x78\x9f\x3e\x22": "tnef (winmail.dat)",
}


def signature(data: bytes) -> str:
    """Format reconnu d'après les premiers octets, ou « ? »."""
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "heic"
    for magic, name in SIGNATURES.items():
        if data.startswith(magic):
            return name
    return "?"


def inspect(path: str) -> None:
    """Affiche une ligne par partie du mail."""
    with open(path, "rb") as f:
        msg = BytesParser(policy=policy.default).parse(f)
    print(f"== {posixpath.basename(path.replace(chr(92), '/'))}")
    for depth, part in walk(msg, 0):
        name = part.get_filename()
        ext = (posixpath.splitext(name)[1].lower() or "(sans extension)") if name else "AUCUN"
        line = f"{'  ' * depth}{part.get_content_type():<32} disposition={part.get_content_disposition() or '-':<10} nom={ext:<16}"
        if part.get("content-id"):
            line += " cid"
        if not part.is_multipart() and part.get_content_type() != "message/rfc822":
            data = part.get_payload(decode=True) or b""
            line += f" {len(data)} octets, signature={signature(data)}"
        print(line)


def walk(part, depth):
    """Parties du mail avec leur profondeur (sans descendre dans un mail joint)."""
    yield depth, part
    if part.is_multipart() and part.get_content_type() != "message/rfc822":
        for sub in part.iter_parts():
            yield from walk(sub, depth + 1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    for p in sys.argv[1:]:
        inspect(p)
