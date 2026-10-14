"""Génère des mails .eml FICTIFS pour tester le traitement des mails.

- mail-facture-optique.eml : une patiente écrit à SG Santé (adresse générique) avec un gestionnaire
  nominatif en copie ; corps HTML (gras, lien mailto, image distante de suivi, image intégrée) et
  la facture d'optique fictive en pièce jointe, dont le nom contient le nom de la patiente.
- mail-sans-piece-jointe.eml : réponse d'un gestionnaire Howden, corps texte seul.

Toutes les personnes et coordonnées sont inventées (e-mails en example.com, téléphones dans les
tranches réservées à la fiction) ; les domaines sgsante.fr et howden-france.com sont ceux des
organisations à ne jamais pseudonymiser, pour vérifier qu'ils sont conservés.

Usage (depuis la racine du projet, après generer_factures.py) :
    uv run --python 3.12 --with pymupdf python exemples/generer_mails.py
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent


def logo_png() -> bytes:
    """Petite image intégrée au corps HTML (logo fictif, sans donnée personnelle)."""
    doc = pymupdf.open()
    page = doc.new_page(width=160, height=40)
    page.draw_rect(page.rect, color=None, fill=(0.12, 0.31, 0.47))
    page.insert_text((10, 26), "Mutuelle fictive", fontsize=13, color=(1, 1, 1))
    return page.get_pixmap(dpi=150).tobytes("png")


def mail_optique() -> EmailMessage:
    """Mail d'une patiente avec facture d'optique jointe et image intégrée."""
    msg = EmailMessage()
    msg["From"] = "Camille ROUSSEL <camille.roussel90@example.com>"
    msg["To"] = "SG Santé <contact@sgsante.fr>"
    msg["Cc"] = "Thomas Lefort <thomas.lefort@sgsante.fr>"
    msg["Subject"] = "Remboursement lunettes - Camille ROUSSEL"
    msg["Date"] = "Mon, 12 Oct 2026 18:42:00 +0200"
    msg["Message-ID"] = make_msgid(domain="example.com")
    msg.set_content(
        "Bonjour,\n\nVeuillez trouver ci-joint la facture de mes nouvelles lunettes.\n"
        "Mon numéro de sécurité sociale : 2 90 11 33 063 045 18.\n"
        "Vous pouvez me joindre au 06 39 98 70 03.\n\nCordialement,\nCamille ROUSSEL\n"
        "8 impasse des Vignes, 33400 Talence\n"
    )
    logo_cid = make_msgid(domain="example.com")
    msg.add_alternative(
        "<html><head><meta charset=\"utf-8\"></head><body>"
        f'<p><img src="cid:{logo_cid[1:-1]}" alt="Logo"></p>'
        "<p>Bonjour,</p><p>Veuillez trouver ci-joint la facture de mes nouvelles lunettes.</p>"
        "<p>Mon numéro de sécurité sociale : <b>2 90 11 33 063 045 18</b>.</p>"
        "<p>Vous pouvez me joindre au 06 39 98 70 03 ou par "
        '<a href="mailto:camille.roussel90@example.com" title="Écrire à Camille ROUSSEL">e-mail</a>.</p>'
        "<p>Cordialement,<br>Camille ROUSSEL<br>8 impasse des Vignes, 33400 Talence</p>"
        '<img src="https://suivi.example.net/ouverture.gif?destinataire=camille.roussel90@example.com" width="1" height="1">'
        "</body></html>",
        subtype="html",
    )
    msg.get_body(("html",)).add_related(logo_png(), maintype="image", subtype="png", cid=logo_cid)
    msg.add_attachment(
        (OUT / "facture-optique.pdf").read_bytes(), maintype="application", subtype="pdf",
        filename="facture_lunettes_ROUSSEL_Camille.pdf",
    )
    return msg


def mail_sans_piece_jointe() -> EmailMessage:
    """Réponse d'un gestionnaire Howden, sans pièce jointe, corps texte seul."""
    msg = EmailMessage()
    msg["From"] = "Julie Masson <julie.masson@howden-france.com>"
    msg["To"] = "Camille ROUSSEL <camille.roussel90@example.com>"
    msg["Reply-To"] = "sinistres@howden-france.com"
    msg["Subject"] = "RE: Remboursement lunettes - Camille ROUSSEL"
    msg["Date"] = "Tue, 13 Oct 2026 10:05:00 +0200"
    msg["Message-ID"] = make_msgid(domain="example.com")
    msg.set_content(
        "Bonjour Madame ROUSSEL,\n\nNous avons bien reçu votre facture du 02/10/2026.\n"
        "Le remboursement sera versé sous 5 jours ouvrés.\n\n"
        "Bien cordialement,\nJulie Masson\nService prestations - Howden France\n"
    )
    return msg


def main() -> None:
    """Écrit les mails fictifs dans exemples/."""
    for name, msg in [("mail-facture-optique.eml", mail_optique()), ("mail-sans-piece-jointe.eml", mail_sans_piece_jointe())]:
        (OUT / name).write_bytes(msg.as_bytes())
        print(name)


if __name__ == "__main__":
    main()
