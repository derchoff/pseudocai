"""Extrait les pièces jointes d'un mail .eml dans un dossier, en reconnaissant leur format réel.

Usage (depuis la racine du projet) :
    uv run --python 3.12 python scripts/extraire-pieces-jointes.py exemples/mail-facture-optique.eml
    uv run --python 3.12 python scripts/extraire-pieces-jointes.py mail.eml --dossier sortie
    uv run --python 3.12 python scripts/extraire-pieces-jointes.py mail.eml --non-prises-en-charge

Par défaut, les pièces sont écrites dans « <nom du mail>_pieces/ », à côté du .eml.
- Les pièces extraites sont celles que voit le service de pseudonymisation, dans le même ordre
  (« pièce N » = numéro de pieces_refusees dans motif.json) : pièces jointes, images intégrées au
  corps (logos, icônes de signature), mails joints. --sans-integrees ignore les images intégrées
  sans nom ; --non-prises-en-charge n'extrait que les pièces que le service refuse.
- Le format réel est reconnu par les premiers octets (PDF, JPEG, PNG, GIF, TIFF, WebP, HEIC, BMP,
  Word/Excel/PowerPoint, OpenDocument, ZIP, ancien Office/Outlook, winmail.dat, mail, HTML, texte).
  Une pièce sans extension, ou en .bin/.dat (application/octet-stream sans nom), reçoit
  l'extension de son format réel : elle s'ouvre directement.
- Pour chaque pièce, une ligne indique : type MIME déclaré, format réel, taille et si le service la
  prend en charge (sinon pourquoi). Les GIF et les images de moins de --seuil-images pixels sur un
  côté (300 par défaut, comme MAIL_IMAGE_MIN_SIDE) sont signalés « retirés du mail » : ils ne
  bloquent pas le mail.
- Les noms de fichiers encodés (=?UTF-8?…?=) sont décodés ; les caractères interdits sous Windows
  et les chemins (« ../ ») sont neutralisés ; deux pièces de même nom ne s'écrasent pas.
- Un mail joint (message/rfc822) est extrait en .eml ; --recursif extrait aussi ses propres pièces,
  dans un sous-dossier.

Bibliothèque standard uniquement : aucune dépendance à installer.
"""

from __future__ import annotations

import argparse
import io
import posixpath
import re
import struct
import sys
import zipfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
NOMS_RESERVES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
EXTENSIONS = {
    "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/tiff": ".tif", "image/webp": ".webp", "image/heic": ".heic", "image/bmp": ".bmp",
    "message/rfc822": ".eml", "text/plain": ".txt", "text/html": ".html",
}
EXTENSIONS_SANS_SENS = {"", ".bin", ".dat", ".tmp", ".null"}

# Règles du service (src/pseudo/formats.py) : format déduit de l'extension du nom ; pour une pièce
# sans nom, du type MIME (liste ci-dessous), sinon « .bin ».
SERVICE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp"}
SERVICE_TYPES_MIME = {
    "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif",
    "image/webp": ".webp", "image/bmp": ".bmp", "image/heic": ".heic", "image/heif": ".heif",
}


# Mails : le service retire les GIF et les images de moins de MAIL_IMAGE_MIN_SIDE pixels sur un côté.
MAIL_IMAGE_MIN_SIDE = 300


# --------------------------------------------------------------------------- format réel

def dimensions(data: bytes) -> tuple[int, int] | None:
    """(largeur, hauteur) d'une image PNG, GIF, JPEG ou BMP, lues dans l'en-tête ; None sinon."""
    try:
        if data[:4] == bytes([0x89]) + b"PNG" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", data[6:10])
        if data.startswith(b"BM") and len(data) >= 26:
            w, h = struct.unpack("<ii", data[18:26])
            return abs(w), abs(h)
        if data.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + length
    except struct.error:
        return None
    return None


def format_reel(data: bytes) -> tuple[str, str]:
    """(extension, libellé) du format reconnu par les premiers octets ; (« », « inconnu ») sinon."""
    debut = data[:16]
    if debut.startswith(b"%PDF-"):
        return ".pdf", "PDF"
    if debut.startswith(b"\xff\xd8\xff"):
        return ".jpg", "JPEG"
    if debut.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "PNG"
    if debut.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "GIF"
    if debut.startswith((b"II*\x00", b"MM\x00*")):
        return ".tif", "TIFF"
    if debut.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "WebP"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heif"):
        return ".heic", "HEIC"
    if debut.startswith(b"BM") and len(data) > 26:
        return ".bmp", "BMP"
    if debut.startswith(b"PK\x03\x04"):
        return _format_zip(data)
    if debut.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ".ole", "ancien Office (.doc/.xls/.ppt) ou message Outlook (.msg)"
    if debut.startswith(b"\x78\x9f\x3e\x22"):
        return ".dat", "winmail.dat (Outlook TNEF)"
    texte = data[:2048].lstrip().lower()
    if texte.startswith((b"<!doctype html", b"<html")):
        return ".html", "HTML"
    if re.match(rb"(received|from|return-path|message-id|mime-version|date|subject):", texte):
        return ".eml", "mail"
    try:
        data[:4096].decode("utf-8")
        return ".txt", "texte"
    except UnicodeDecodeError:
        return "", "inconnu"


def _format_zip(data: bytes) -> tuple[str, str]:
    """Distingue les formats bâtis sur ZIP : Office récent, OpenDocument, iWork, archive ZIP."""
    try:
        noms = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    except zipfile.BadZipFile:
        return ".zip", "ZIP (abîmé)"
    if "word/document.xml" in noms:
        return ".docx", "Word"
    if "xl/workbook.xml" in noms:
        return ".xlsx", "Excel"
    if "ppt/presentation.xml" in noms:
        return ".pptx", "PowerPoint"
    if "mimetype" in noms:
        return ".odt", "OpenDocument"
    if any(n.startswith("Index/") for n in noms):
        return ".zip", "iWork (Pages/Numbers/Keynote)"
    return ".zip", "archive ZIP"


# --------------------------------------------------------------------------- pièces du mail

def lire_mail(chemin: Path) -> EmailMessage:
    """Lit un fichier .eml (en-têtes décodés selon les règles actuelles des mails)."""
    with chemin.open("rb") as f:
        return BytesParser(policy=policy.default).parse(f)


def _feuilles(partie: EmailMessage):
    """Parties terminales ; un mail joint est rendu tel quel, sans y descendre."""
    if partie.get_content_type() == "message/rfc822":
        yield partie
    elif partie.is_multipart():
        for enfant in partie.iter_parts():
            yield from _feuilles(enfant)
    else:
        yield partie


def pieces(mail: EmailMessage):
    """Pièces du mail, comme les voit le service : tout sauf le corps (texte et HTML).

    Renvoie (numéro de service, partie) ; un mail joint a le numéro None (le service le refuse
    à part, sans numéro).
    """
    corps = mail.get_body(preferencelist=("html", "plain"))
    numero = 0
    for partie in _feuilles(mail):
        if partie is mail or partie is corps:
            continue
        if partie.get_content_type() == "message/rfc822":
            yield None, partie
            continue
        if (partie.get_content_type() in ("text/plain", "text/html") and not partie.get_filename()
                and not partie.is_attachment()):
            continue  # autre version du corps
        if partie.get_payload(decode=True) is None:
            continue
        numero += 1
        yield numero, partie


def nom_declare(partie: EmailMessage) -> str | None:
    """Nom déclaré de la pièce (sans dossier) ; pour un mail joint sans nom, son objet."""
    nom = partie.get_filename()
    if not nom and partie.get_content_type() == "message/rfc822":
        interne = partie.get_payload(0) if partie.is_multipart() else None
        objet = (interne.get("subject") if interne is not None else None) or ""
        nom = f"{objet}.eml" if objet.strip() else None
    return nom.replace("\\", "/").split("/")[-1].strip() if nom else None


def contenu(partie: EmailMessage) -> bytes:
    """Octets de la partie, décodés (base64, quoted-printable) ; un mail joint est réécrit en .eml."""
    if partie.get_content_type() == "message/rfc822":
        interne = partie.get_payload(0) if partie.is_multipart() else partie.get_content()
        return interne.as_bytes(policy=policy.default)
    return partie.get_payload(decode=True) or b""


def statut_service(numero: int | None, partie: EmailMessage, donnees: bytes = b"",
                   seuil: int = MAIL_IMAGE_MIN_SIDE) -> tuple[bool, str]:
    """(pris en charge, explication) selon les règles du service. Une image retirée par le service
    (GIF, ou plus petite que seuil pixels sur un côté) ne bloque pas le mail : « pris en charge »."""
    if numero is None:
        return False, "mail joint"
    nom_bas = (partie.get_filename() or "").lower()
    if nom_bas.endswith(".gif") or partie.get_content_type() == "image/gif" or donnees[:6] in (b"GIF87a", b"GIF89a"):
        return True, "GIF : retiré du mail par le service"
    taille = dimensions(donnees)
    if taille and min(taille) < seuil:
        return True, f"image de {taille[0]}x{taille[1]} px (< {seuil}) : retirée du mail par le service"
    nom = partie.get_filename()
    if nom:
        ext = posixpath.splitext(nom.replace("\\", "/"))[1].lower()
        if ext in SERVICE_EXTENSIONS:
            return True, f"extension {ext}"
        return False, f"extension « {ext or 'aucune'} » non prise en charge"
    ext = SERVICE_TYPES_MIME.get(partie.get_content_type())
    if ext:
        return True, f"sans nom, type MIME {partie.get_content_type()}"
    return False, f"sans nom, type MIME {partie.get_content_type()} inconnu du service (« .bin »)"


# --------------------------------------------------------------------------- écriture

def nom_sur(nom: str | None, partie: EmailMessage, numero: int, ext_reelle: str) -> str:
    """Nom de fichier utilisable sous Windows, avec une extension qui permet de l'ouvrir."""
    nom = INTERDITS.sub("_", (nom or "").strip().strip("."))
    base, ext = (nom.rsplit(".", 1) + [""])[:2] if "." in nom else (nom, "")
    ext = f".{ext.lower()}" if ext else ""
    if not base:
        base = f"piece-{numero:02d}"
    if base.upper() in NOMS_RESERVES:
        base = f"_{base}"
    if ext in EXTENSIONS_SANS_SENS:
        ext = ext_reelle or EXTENSIONS.get(partie.get_content_type(), ".bin")
    return f"{base[:150]}{ext}"


def chemin_libre(dossier: Path, nom: str) -> Path:
    """Chemin qui n'écrase rien : « nom.pdf », sinon « nom (2).pdf », « nom (3).pdf »…"""
    cible = dossier / nom
    stem, suffix = cible.stem, cible.suffix
    n = 2
    while cible.exists():
        cible = dossier / f"{stem} ({n}){suffix}"
        n += 1
    return cible


def extraire(chemin: Path, dossier: Path, args, prefixe: str = "") -> tuple[int, int]:
    """Extrait les pièces du mail ; renvoie (fichiers écrits, pièces non prises en charge)."""
    ecrits = refusees = 0
    for rang, (numero, partie) in enumerate(pieces(lire_mail(chemin)), start=1):
        nom = nom_declare(partie)
        integree = bool(partie.get("content-id")) and not nom
        if args.sans_integrees and integree:
            continue
        donnees = contenu(partie)
        ext_reelle, libelle = (".eml", "mail") if numero is None else format_reel(donnees)
        pris, raison = statut_service(numero, partie, donnees, args.seuil_images)
        refusees += not pris
        if args.non_prises_en_charge and pris:
            continue
        dossier.mkdir(parents=True, exist_ok=True)
        cible = chemin_libre(dossier, nom_sur(nom, partie, numero or rang, ext_reelle))
        cible.write_bytes(donnees)
        ecrits += 1
        taille = f"{len(donnees):,}".replace(",", " ")
        etiquette = f"pièce {numero}" if numero else "mail joint"
        print(f"{prefixe}[{etiquette}] {cible.name}")
        print(f"{prefixe}    déclaré : {partie.get_content_type()}, {partie.get_content_disposition() or '-'}"
              f"{', image intégrée au corps' if integree else ''} | réel : {libelle} | {taille} octets")
        print(f"{prefixe}    service : {'pris en charge' if pris else 'NON pris en charge'} ({raison})")
        if args.recursif and numero is None:
            e, r = extraire(cible, dossier / f"{cible.stem}_pieces", args, prefixe + "  ")
            ecrits, refusees = ecrits + e, refusees + r
    return ecrits, refusees


def main() -> int:
    """Point d'entrée : extrait les pièces jointes du mail donné en argument."""
    parser = argparse.ArgumentParser(description="Extrait les pièces jointes d'un fichier .eml en reconnaissant leur format")
    parser.add_argument("eml", type=Path, help="fichier .eml à lire")
    parser.add_argument("--dossier", type=Path, help="dossier de sortie (défaut : <nom du mail>_pieces)")
    parser.add_argument("--sans-integrees", action="store_true",
                        help="ignorer les images intégrées au corps sans nom (logos, icônes de signature)")
    parser.add_argument("--non-prises-en-charge", action="store_true",
                        help="n'extraire que les pièces que le service refuse")
    parser.add_argument("--recursif", action="store_true", help="extraire aussi les pièces des mails joints")
    parser.add_argument("--seuil-images", type=int, default=MAIL_IMAGE_MIN_SIDE,
                        help="MAIL_IMAGE_MIN_SIDE du service (défaut 300) : image plus petite = retirée du mail")
    parser.add_argument("--inline", action="store_true", help=argparse.SUPPRESS)  # ancien réglage : c'est le défaut
    args = parser.parse_args()

    if not args.eml.is_file():
        print(f"Fichier introuvable : {args.eml}", file=sys.stderr)
        return 1
    dossier = args.dossier or args.eml.with_name(f"{args.eml.stem}_pieces")
    mail = lire_mail(args.eml)
    print(f"Mail : {mail.get('subject', '(sans objet)')}")
    print(f"De   : {mail.get('from', '?')}  —  le {mail.get('date', '?')}")
    ecrits, refusees = extraire(args.eml, dossier, args)
    if ecrits:
        print(f"{ecrits} fichier(s) extrait(s) dans {dossier}")
    else:
        print("Aucune pièce extraite.")
    if refusees:
        print(f"{refusees} pièce(s) NON prise(s) en charge par le service (le mail part en quarantaine).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
