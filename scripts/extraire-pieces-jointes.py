"""Extrait les pièces jointes d'un mail .eml dans un dossier.

Usage (depuis la racine du projet) :
    uv run --python 3.12 python scripts/extraire-pieces-jointes.py exemples/mail-facture-optique.eml
    uv run --python 3.12 python scripts/extraire-pieces-jointes.py mail.eml --dossier sortie --inline

Par défaut, les pièces sont écrites dans « <nom du mail>_pieces/ », à côté du .eml.
- Les noms de fichiers encodés (=?UTF-8?…?=) sont décodés ; les caractères interdits sous Windows
  et les chemins (« ../ ») sont neutralisés ; deux pièces de même nom ne s'écrasent pas.
- Toute partie qui porte un nom de fichier est extraite, même déclarée « inline » (photos jointes
  depuis un iPhone, Outlook, Gmail) ; --inline ajoute les images SANS nom du corps (logos, signatures).
- Un mail joint à un mail (message/rfc822) est extrait en .eml ; --recursif extrait aussi ses
  propres pièces jointes, dans un sous-dossier.

Bibliothèque standard uniquement : aucune dépendance à installer.
"""

from __future__ import annotations

import argparse
import re
import sys
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
NOMS_RESERVES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
EXTENSIONS = {
    "application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
    "image/tiff": ".tif", "image/webp": ".webp", "image/heic": ".heic", "message/rfc822": ".eml",
    "text/plain": ".txt", "text/html": ".html",
}


def lire_mail(chemin: Path) -> EmailMessage:
    """Lit un fichier .eml (en-têtes décodés selon les règles actuelles des mails)."""
    with chemin.open("rb") as f:
        return BytesParser(policy=policy.default).parse(f)


def nom_sur(nom: str | None, partie: EmailMessage, numero: int) -> str:
    """Nom de fichier utilisable sous Windows : sans dossier, sans caractère interdit, avec extension."""
    nom = (nom or "").replace("\\", "/").split("/")[-1].strip().strip(".")
    nom = INTERDITS.sub("_", nom)
    if not nom:
        nom = f"piece-{numero:02d}"
    base, point, ext = nom.rpartition(".")
    if not point:
        base, ext = nom, ""
    if base.upper() in NOMS_RESERVES:
        base = f"_{base}"
    if not ext:
        ext = EXTENSIONS.get(partie.get_content_type(), ".bin").lstrip(".")
    return f"{base[:150]}.{ext}"


def chemin_libre(dossier: Path, nom: str) -> Path:
    """Chemin qui n'écrase rien : « nom.pdf », sinon « nom (2).pdf », « nom (3).pdf »…"""
    cible = dossier / nom
    stem, suffix = cible.stem, cible.suffix
    n = 2
    while cible.exists():
        cible = dossier / f"{stem} ({n}){suffix}"
        n += 1
    return cible


def pieces(partie: EmailMessage, inline: bool, racine: bool = True):
    """Parties du mail à extraire : pièces jointes, mails joints et, avec inline, images intégrées.

    - Une partie qui porte un nom de fichier est une pièce jointe, quelle que soit sa disposition :
      iPhone, Outlook et Gmail déclarent souvent les photos jointes « inline » (affichées dans le
      corps), avec leur nom (IMG_0421.JPG).
    - Une image SANS nom (logo, signature appelés par cid: dans le corps HTML) n'est extraite
      qu'avec --inline.
    - Un mail joint est rendu tel quel, sans descendre dedans : ses propres pièces ne sont
      extraites qu'avec --recursif, depuis le .eml écrit.
    """
    if partie.get_content_type() == "message/rfc822" and not racine:
        yield partie
    elif partie.is_multipart():
        for enfant in partie.iter_parts():
            yield from pieces(enfant, inline, racine=False)
    elif not racine:
        if partie.get_content_disposition() == "attachment" or partie.get_filename():
            yield partie
        elif inline and partie.get_content_maintype() == "image":
            yield partie


def nom_propose(partie: EmailMessage) -> str | None:
    """Nom déclaré de la pièce ; pour un mail joint sans nom, son objet."""
    nom = partie.get_filename()
    if not nom and partie.get_content_type() == "message/rfc822":
        interne = partie.get_payload(0) if partie.is_multipart() else None
        objet = (interne.get("subject") if interne is not None else None) or ""
        nom = f"{objet}.eml" if objet.strip() else None
    return nom


def contenu(partie: EmailMessage) -> bytes:
    """Octets de la partie, décodés (base64, quoted-printable) ; un mail joint est réécrit en .eml."""
    if partie.get_content_type() == "message/rfc822":
        interne = partie.get_payload(0) if partie.is_multipart() else partie.get_content()
        return interne.as_bytes(policy=policy.default)
    return partie.get_payload(decode=True) or b""


def extraire(chemin: Path, dossier: Path, inline: bool, recursif: bool, prefixe: str = "") -> int:
    """Extrait les pièces du mail chemin dans dossier ; renvoie le nombre de fichiers écrits."""
    mail = lire_mail(chemin)
    total = 0
    for numero, partie in enumerate(pieces(mail, inline), start=1):
        dossier.mkdir(parents=True, exist_ok=True)
        cible = chemin_libre(dossier, nom_sur(nom_propose(partie), partie, numero))
        donnees = contenu(partie)
        cible.write_bytes(donnees)
        total += 1
        taille = f"{len(donnees):,}".replace(",", " ")
        print(f"{prefixe}{cible.name}  ({partie.get_content_type()}, {taille} octets)")
        if recursif and partie.get_content_type() == "message/rfc822":
            total += extraire(cible, dossier / f"{cible.stem}_pieces", inline, recursif, prefixe + "  ")
    return total


def main() -> int:
    """Point d'entrée : extrait les pièces jointes du mail donné en argument."""
    parser = argparse.ArgumentParser(description="Extrait les pièces jointes d'un fichier .eml")
    parser.add_argument("eml", type=Path, help="fichier .eml à lire")
    parser.add_argument("--dossier", type=Path, help="dossier de sortie (défaut : <nom du mail>_pieces)")
    parser.add_argument("--inline", action="store_true", help="extraire aussi les images sans nom intégrées au corps (logos, signatures)")
    parser.add_argument("--recursif", action="store_true", help="extraire aussi les pièces des mails joints")
    args = parser.parse_args()

    if not args.eml.is_file():
        print(f"Fichier introuvable : {args.eml}", file=sys.stderr)
        return 1
    dossier = args.dossier or args.eml.with_name(f"{args.eml.stem}_pieces")
    mail = lire_mail(args.eml)
    print(f"Mail : {mail.get('subject', '(sans objet)')}")
    print(f"De   : {mail.get('from', '?')}  —  le {mail.get('date', '?')}")
    total = extraire(args.eml, dossier, args.inline, args.recursif)
    if total:
        print(f"{total} fichier(s) extrait(s) dans {dossier}")
    else:
        print("Aucune pièce jointe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
