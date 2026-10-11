"""Génère des factures de santé FICTIVES pour tester le service de pseudonymisation.

Toutes les données sont inventées :
- téléphones dans les tranches réservées à la fiction par l'ARCEP (01 99 00, 06 39 98...) ;
- e-mails sur le domaine réservé example.com ;
- NIR fabriqués avec une clé de contrôle valide (pour tester le filet regex), sans lien
  avec une personne réelle ;
- numéros RPPS / ADELI / SIRET inventés.

Usage (depuis la racine du projet) :
    uv run --python 3.12 --with pymupdf --with pillow python exemples/generer_factures.py
"""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent
A4 = pymupdf.paper_rect("a4")
MARGIN = 42

CSS = """
* { font-family: sans-serif; font-size: 9.5pt; color: #222; }
h1 { font-size: 17pt; color: #1f4e79; margin: 0 0 2pt 0; }
h2 { font-size: 10.5pt; color: #1f4e79; margin: 10pt 0 3pt 0; border-bottom: 1px solid #1f4e79; }
.small { font-size: 8pt; color: #555; }
.right { text-align: right; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #dce6f1; text-align: left; padding: 3pt; font-size: 8.5pt; }
td { padding: 3pt; border-bottom: 1px solid #ddd; font-size: 8.5pt; }
td.num, th.num { text-align: right; }
.total td { font-weight: bold; border-bottom: none; }
.box { border: 1px solid #bbb; padding: 5pt; }
.sign { font-style: italic; font-size: 12pt; color: #1f3a60; }
.fictif { font-size: 7.5pt; color: #a00; text-align: center; }
"""

FICTIF = (
    "DOCUMENT FICTIF généré pour tester un service de pseudonymisation — "
    "toutes les personnes, coordonnées et numéros sont inventés."
)


def nir_key(first13: str) -> str:
    n = int(first13.upper().replace("2A", "19").replace("2B", "18"))
    return f"{97 - n % 97:02d}"


def nir(sex, yy, mm, dept, commune, ordre) -> str:
    first13 = f"{sex}{yy}{mm}{dept}{commune}{ordre}"
    return f"{sex} {yy} {mm} {dept} {commune} {ordre} {nir_key(first13)}"


def euro(value: float) -> str:
    return f"{value:,.2f} €".replace(",", " ").replace(".", ",")


def rows_html(rows) -> str:
    return "".join(
        "<tr>" + "".join(f'<td class="{c}">{v}</td>' for c, v in zip(("", "", "", "num", "num"), r)) + "</tr>"
        for r in rows
    )


def header_left_html(d: dict) -> str:
    title = "" if d.get("logo") else f"<h1>{d['cabinet']}</h1>"
    return f"""{title}
  <b>{d['praticien']}</b><br/>{d['specialite']}<br/>
  {d['adresse_cabinet']}<br/>
  Tél. {d['tel_cabinet']} — {d['email_cabinet']}<br/>
  <span class="small">{d['identifiants']}</span>"""


def header_right_html(d: dict) -> str:
    return f"""<div class="right"><h1>FACTURE</h1>
  N° {d['numero']}<br/>Date : {d['date']}<br/>
  <span class="small">{d.get('mention_droite', '')}</span></div>"""


def invoice_html(d: dict) -> str:
    total = sum(r[4] for r in d["actes"])
    rows = [(r[0], r[1], r[2], r[3], euro(r[4])) for r in d["actes"]]
    amo = d.get("amo", 0.0)
    amc = d.get("amc", 0.0)
    reste = total - amo - amc
    return f"""
<h2>Patient</h2>
<div class="box">
  {d['civilite']} <b>{d['nom'].upper()} {d['prenom']}</b><br/>
  Né{d.get('accord', '')} le {d['naissance']} — N° de sécurité sociale : {d['nir']}<br/>
  {d['adresse_patient']}<br/>
  Tél. {d['tel_patient']} — {d['email_patient']}<br/>
  {d.get('mutuelle', '')}
</div>

{d.get('bloc_prescription', '')}

<h2>{d.get('titre_actes', 'Actes réalisés')}</h2>
<table>
<tr><th>Date</th><th>Code</th><th>Désignation</th><th class="num">{d.get('col_qte', 'Qté')}</th><th class="num">Montant</th></tr>
{rows_html(rows)}
<tr class="total"><td></td><td></td><td>Total honoraires</td><td></td><td class="num">{euro(total)}</td></tr>
</table>

<table style="margin-top:6pt;">
<tr><td style="border:none; width:55%; vertical-align:top;" class="small">{d.get('notes', '')}</td>
<td style="border:none; vertical-align:top;">
  <table>
  <tr><td>Part Assurance Maladie (estimée)</td><td class="num">{euro(amo)}</td></tr>
  <tr><td>Part complémentaire (estimée)</td><td class="num">{euro(amc)}</td></tr>
  <tr class="total"><td>Reste à charge</td><td class="num">{euro(reste)}</td></tr>
  </table>
</td></tr></table>

<p>{d['reglement']}</p>
<p class="right">{d['ville_signature']}, le {d['date']}<br/><span class="sign">{d['signature']}</span></p>
"""


def make_logo(text: str, subtitle: str) -> pymupdf.Pixmap:
    """Logo raster contenant le nom du praticien : il n'existe pas dans la couche texte du PDF."""
    tmp = pymupdf.open()
    page = tmp.new_page(width=230, height=48)
    page.draw_rect(page.rect, color=None, fill=(0.12, 0.31, 0.47))
    page.insert_text((12, 28), text, fontsize=19, fontname="hebo", color=(1, 1, 1))
    page.insert_text((12, 42), subtitle, fontsize=8.5, fontname="helv", color=(0.85, 0.92, 1))
    return page.get_pixmap(dpi=200)


def build_pdf(d: dict, path: Path) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    top = MARGIN
    if d.get("logo"):
        logo_rect = pymupdf.Rect(MARGIN, MARGIN, MARGIN + 230, MARGIN + 48)
        page.insert_image(logo_rect, pixmap=make_logo(*d["logo"]))
        top = logo_rect.y1 + 6
    width = A4.width - 2 * MARGIN
    left = pymupdf.Rect(MARGIN, top, MARGIN + width * 0.62, top + 140)
    right = pymupdf.Rect(MARGIN + width * 0.62, MARGIN, A4.width - MARGIN, MARGIN + 140)
    spare_left, _ = page.insert_htmlbox(left, header_left_html(d), css=CSS)
    spare_right, _ = page.insert_htmlbox(right, header_right_html(d), css=CSS)
    body_top = max(left.y1 - spare_left, right.y1 - spare_right) + 4
    body = pymupdf.Rect(MARGIN, body_top, A4.width - MARGIN, A4.height - MARGIN - 20)
    page.insert_htmlbox(body, invoice_html(d), css=CSS)
    footer = pymupdf.Rect(MARGIN, A4.height - MARGIN - 16, A4.width - MARGIN, A4.height - MARGIN + 4)
    page.insert_htmlbox(footer, f'<p class="fictif">{FICTIF}</p>', css=CSS)
    doc.set_metadata({"title": f"Facture {d['numero']} (fictive)", "author": "Générateur de test"})
    data = doc.tobytes(garbage=3, deflate=True)
    path.write_bytes(data)
    return data


def make_scan(pdf_bytes: bytes, path: Path, angle: float = 1.8) -> None:
    """Version « scannée » : image légèrement tournée, grisée et bruitée, sans couche texte."""
    from PIL import Image, ImageFilter, ImageOps
    import random

    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pix = src[0].get_pixmap(dpi=150)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    rnd = random.Random(42)
    px = img.load()
    for _ in range(img.width * img.height // 60):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        px[x, y] = max(0, px[x, y] - rnd.randrange(40, 120))
    img = ImageOps.autocontrast(img, cutoff=1)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    scan = pymupdf.open()
    page = scan.new_page(width=A4.width, height=A4.height)
    page.insert_image(page.rect, stream=buf.getvalue())
    path.write_bytes(scan.tobytes(deflate=True))


FACTURES = {
    "facture-dentiste": dict(
        cabinet="Cabinet dentaire du Parc",
        praticien="Dr Élise MARCHAND",
        specialite="Chirurgien-dentiste — Docteur en chirurgie dentaire",
        adresse_cabinet="14 avenue des Tilleuls, 69006 Lyon",
        tel_cabinet="04 65 71 12 34",
        email_cabinet="cabinet.marchand@example.com",
        identifiants="RPPS 10100456789 — SIRET 812 345 678 00019 — Conventionnée secteur 1",
        numero="D-2026-0418",
        date="06/10/2026",
        civilite="M.",
        nom="Lefèvre",
        prenom="Julien",
        naissance="14/06/1982",
        nir=nir(1, "82", "06", "69", "123", "456"),
        adresse_patient="27 rue Paul Bert, Bât. B, Apt 12, 69003 Lyon",
        tel_patient="06 39 98 45 12",
        email_patient="julien.lefevre@example.com",
        mutuelle="Mutuelle : Horizon Santé — Adhérent n° HS-4471-2290",
        titre_actes="Actes réalisés (2 dents)",
        col_qte="Dent",
        actes=[
            ("22/09/2026", "HBMD049", "Restauration d'une dent sur 3 faces (composite)", "16", 95.00),
            ("22/09/2026", "HBQK002", "Radiographie rétro-alvéolaire", "36", 20.00),
            ("06/10/2026", "HBLD634", "Couronne dentoportée céramométallique", "36", 500.00),
            ("06/10/2026", "HBMD058", "Reconstitution pré-prothétique (inlay-core)", "36", 230.00),
        ],
        amo=153.30,
        amc=480.00,
        notes="Couronne : prothèse panier « reste à charge maîtrisé ». Devis signé le 15/09/2026. "
              "Prochain contrôle conseillé dans 6 mois.",
        reglement="Réglé par carte bancaire le 06/10/2026. Feuille de soins électronique transmise.",
        ville_signature="Lyon",
        signature="Dr É. Marchand",
    ),
    "facture-optique": dict(
        logo=("OPTIQUE N. BERTIN", "Opticien diplômé — Visagiste"),
        cabinet="",
        praticien="Nicolas BERTIN",
        specialite="Opticien-lunetier diplômé",
        adresse_cabinet="3 place du Marché, 33000 Bordeaux",
        tel_cabinet="05 36 49 22 10",
        email_cabinet="contact@optique-bertin.example.com",
        identifiants="N° ADELI 339876543 — SIRET 798 456 123 00027",
        numero="OPT-2026-1127",
        date="02/10/2026",
        mention_droite="Remplacement d'équipement — Classe B",
        civilite="Mme",
        accord="e",
        nom="Roussel",
        prenom="Camille",
        naissance="03/11/1990",
        nir=nir(2, "90", "11", "33", "063", "045"),
        adresse_patient="8 impasse des Vignes, 33400 Talence",
        tel_patient="06 39 98 70 03",
        email_patient="camille.roussel90@example.com",
        mutuelle="Complémentaire : Alliance Prévoyance — Contrat n° AP-77812",
        bloc_prescription="""
<h2>Prescription</h2>
<div class="box">Ordonnance du Dr Marc LAURENT, ophtalmologiste, du 12/09/2026 —
OD : -2,25 (-0,50 à 90°) ; OG : -2,00 (-0,75 à 85°) ; Écart pupillaire 62 mm.</div>""",
        titre_actes="Équipement délivré (remplacement de lunettes)",
        actes=[
            ("02/10/2026", "2203240", "Monture adulte — Réf. Lindberg Air 5521", "1", 189.00),
            ("02/10/2026", "2259966", "Verre unifocal sphéro-torique OD, antireflet", "1", 135.00),
            ("02/10/2026", "2259966", "Verre unifocal sphéro-torique OG, antireflet", "1", 135.00),
            ("02/10/2026", "—", "Adaptation et ajustage", "1", 0.00),
        ],
        amo=0.09,
        amc=250.00,
        notes="Ancien équipement (2023) cassé. Garantie casse 2 ans incluse. "
              "Retrait de l'équipement le 09/10/2026.",
        reglement="Acompte de 100,00 € versé le 12/09/2026. Solde réglé par chèque le 02/10/2026.",
        ville_signature="Bordeaux",
        signature="N. Bertin",
    ),
    "facture-psychologue": dict(
        cabinet="Cabinet de psychologie",
        praticien="Sophie GARNIER",
        specialite="Psychologue clinicienne — Psychothérapeute",
        adresse_cabinet="51 boulevard Gambetta, 2e étage, 59000 Lille",
        tel_cabinet="03 53 01 64 87",
        email_cabinet="s.garnier.psy@example.com",
        identifiants="N° ADELI 599312345 — SIRET 901 234 567 00012 — Conventionnée « Mon soutien psy »",
        numero="PSY-2026-0093",
        date="30/09/2026",
        civilite="M.",
        nom="Morel",
        prenom="Thomas",
        naissance="22/01/1995",
        nir=nir(1, "95", "01", "59", "350", "210"),
        adresse_patient="112 rue Solférino, 59000 Lille",
        tel_patient="06 39 98 31 58",
        email_patient="thomas.morel@example.com",
        bloc_prescription="""
<h2>Adressage</h2>
<div class="box">Adressé par le Dr Claire DUMONT, médecin généraliste (courrier du 28/08/2026).</div>""",
        titre_actes="Séances",
        actes=[
            ("09/09/2026", "PSY", "Entretien d'évaluation — Dispositif Mon soutien psy", "1", 50.00),
            ("16/09/2026", "PSY", "Séance de suivi psychologique (45 min)", "1", 50.00),
            ("23/09/2026", "PSY", "Séance de suivi psychologique (45 min)", "1", 50.00),
            ("30/09/2026", "PSY", "Séance de suivi psychologique (45 min)", "1", 50.00),
        ],
        amo=120.00,
        amc=80.00,
        notes="Séances 1 à 4 sur 12 du dispositif. Prochaine séance le 07/10/2026 à 18h30.",
        reglement="Tiers payant Assurance Maladie. Part complémentaire réglée en espèces.",
        ville_signature="Lille",
        signature="S. Garnier",
    ),
    "facture-kinesitherapeute": dict(
        cabinet="Kiné Santé Nantes Est",
        praticien="Antoine DUBOIS",
        specialite="Masseur-kinésithérapeute D.E.",
        adresse_cabinet="5 rue de la Loire, 44300 Nantes",
        tel_cabinet="02 61 91 08 42",
        email_cabinet="antoine.dubois.kine@example.com",
        identifiants="RPPS 10101987654 — SIRET 823 765 432 00031 — Conventionné",
        numero="K-2026-0571",
        date="03/10/2026",
        civilite="Mme",
        accord="e",
        nom="Fontaine",
        prenom="Nathalie",
        naissance="8 septembre 1975",
        nir=nir(2, "75", "09", "44", "109", "087"),
        adresse_patient="Résidence Les Pins, 19 allée des Mésanges, 44470 Carquefou",
        tel_patient="+33 6 39 98 12 77",
        email_patient="n.fontaine75@example.com",
        mutuelle="Mutuelle : Solidarité Ouest — Adhérente n° SO-118-5562",
        bloc_prescription="""
<h2>Prescription</h2>
<div class="box">Prescription du Dr Paul HENRY, rhumatologue, du 01/09/2026 :
10 séances de rééducation du rachis lombaire (lombalgie chronique).</div>""",
        titre_actes="Séances de rééducation",
        actes=[
            ("08/09/2026 → 19/09/2026", "AMK 7,5", "Rééducation du rachis — séances 1 à 4", "4", 69.20),
            ("22/09/2026 → 03/10/2026", "AMK 7,5", "Rééducation du rachis — séances 5 à 10", "6", 103.80),
            ("08/09/2026", "IFD", "Indemnité forfaitaire de déplacement (séance à domicile)", "1", 2.50),
        ],
        amo=105.30,
        amc=70.20,
        notes="Bilan de fin de traitement transmis au Dr Henry. "
              "En cas d'empêchement, contacter le cabinet au 02 61 91 08 42.",
        reglement="Tiers payant intégral (AMO + mutuelle). Aucun montant restant dû.",
        ville_signature="Nantes",
        signature="A. Dubois",
    ),
}


def main() -> None:
    for name, data in FACTURES.items():
        pdf = build_pdf(data, OUT / f"{name}.pdf")
        print(f"{name}.pdf")
        if name == "facture-kinesitherapeute":
            make_scan(pdf, OUT / f"{name}-scan.pdf")
            print(f"{name}-scan.pdf")


if __name__ == "__main__":
    main()
