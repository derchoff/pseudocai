"""Génère une carte grise FICTIVE (certificat d'immatriculation) pour tester la pseudonymisation.

Le document reprend les rubriques d'un certificat d'immatriculation (A, B, C.1, C.3, D.1, E…)
mais n'en est PAS une copie : ni Marianne, ni logo officiel, ni élément de sécurité, et la
mention « SPÉCIMEN — DOCUMENT FICTIF » barre la page. Toutes les valeurs sont inventées.

Données personnelles à pseudonymiser : titulaire (C.1), adresse (C.3), et, selon le référentiel,
immatriculation (A), numéro d'identification du véhicule (E) et numéro de formule.

Fichiers produits :
- carte-grise-fictive.pdf        : PDF natif (couche texte)
- photo-carte-grise-fictive.jpg  : photo de travers sur une table (nécessite opencv-python-headless)

Usage (depuis la racine du projet) :
    uv run --python 3.12 --with pymupdf --with pillow --with opencv-python-headless python exemples/generer_carte_grise.py
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent
A4 = pymupdf.paper_rect("a4")
MARGIN = 42

CSS = """
* { font-family: sans-serif; font-size: 8.5pt; color: #222; }
h1 { font-size: 13pt; margin: 0 0 1pt 0; letter-spacing: 1pt; }
.sous-titre { font-size: 8pt; color: #555; margin-bottom: 6pt; }
table { border-collapse: collapse; width: 100%; }
td { padding: 2.2pt 3pt; border-bottom: 1px solid #c9cfc9; vertical-align: top; }
td.code { width: 28pt; font-weight: bold; color: #3d5a3d; }
td.lib { width: 92pt; color: #555; font-size: 7pt; }
td.val { font-weight: bold; }
.cadre { border: 1.5px solid #7a8f7a; background-color: #f1f4f0; padding: 8pt; }
.plaque { font-size: 13pt; font-weight: bold; letter-spacing: 1.5pt; }
.fictif { font-size: 7.5pt; color: #a00; text-align: center; }
"""

FICTIF = (
    "DOCUMENT FICTIF généré pour tester un service de pseudonymisation — ce n'est pas un "
    "certificat d'immatriculation ; toutes les personnes, adresses et numéros sont inventés."
)

# (rubrique, libellé, valeur) — valeurs inventées.
RUBRIQUES_GAUCHE = [
    ("A", "Numéro d'immatriculation", '<span class="plaque">FX-482-KT</span>'),
    ("B", "Date de la 1re immatriculation", "14/03/2019"),
    ("C.1", "Titulaire du certificat (nom, prénom)", "LEMAIRE SOPHIE"),
    ("C.4a", "Mention", "EST LE PROPRIÉTAIRE DU VÉHICULE"),
    ("C.3", "Adresse du titulaire", "12 RUE DES GLYCINES<br/>44000 NANTES"),
    ("C.4.1", "Nombre de titulaires", "1"),
    ("D.1", "Marque", "PEUGEOT"),
    ("D.2", "Type, variante, version", "MPE5Z2KC"),
    ("D.2.1", "Code national d'identification du type", "M10PGTVP003G451"),
    ("D.3", "Dénomination commerciale", "308"),
    ("E", "Numéro d'identification du véhicule", "VF3LBHNZ6KS214587"),
    ("I", "Date de l'immatriculation actuelle", "14/03/2019"),
]

RUBRIQUES_DROITE = [
    ("F.1", "Masse en charge max. techniquement admissible (kg)", "1795"),
    ("F.2", "Masse en charge max. admissible en service (kg)", "1795"),
    ("F.3", "Masse max. de l'ensemble (kg)", "3195"),
    ("G", "Masse en service (kg)", "1395"),
    ("G.1", "Poids à vide national (kg)", "1320"),
    ("J", "Catégorie (CE)", "M1"),
    ("J.1", "Genre national", "VP"),
    ("J.2", "Carrosserie (CE)", "AB"),
    ("J.3", "Carrosserie (désignation nationale)", "CI"),
    ("K", "Numéro de réception par type", "e2*2007/46*0534*12"),
    ("P.1", "Cylindrée (cm³)", "1199"),
    ("P.2", "Puissance nette max. (kW)", "96"),
    ("P.3", "Type de carburant", "ES"),
    ("P.6", "Puissance administrative nationale", "6"),
    ("S.1", "Nombre de places assises", "5"),
    ("V.7", "CO2 (g/km)", "112"),
    ("V.9", "Classe environnementale", "EURO6"),
    ("X.1", "Date de la prochaine visite technique", "14/03/2023"),
    ("Y.6", "Montant total des taxes (€)", "13,76"),
]


def rubriques_html(rows) -> str:
    """Tableau HTML « rubrique | libellé | valeur »."""
    return "<table>" + "".join(
        f'<tr><td class="code">{c}</td><td class="lib">{lib}</td><td class="val">{v}</td></tr>'
        for c, lib, v in rows
    ) + "</table>"


def build_pdf(path: Path) -> bytes:
    """Génère la carte grise fictive (en-tête, deux colonnes de rubriques, filigrane) et l'écrit dans path."""
    doc = pymupdf.open()
    page = doc.new_page(width=A4.width, height=A4.height)
    width = A4.width - 2 * MARGIN

    header = pymupdf.Rect(MARGIN, MARGIN, A4.width - MARGIN, MARGIN + 60)
    page.insert_htmlbox(
        header,
        '<h1>CERTIFICAT D\'IMMATRICULATION — SPÉCIMEN</h1>'
        '<div class="sous-titre">N° de formule : 2019AB45218 — Délivré le 14/03/2019 — '
        "Modèle de test, sans valeur administrative</div>",
        css=CSS,
    )
    frame = pymupdf.Rect(MARGIN, MARGIN + 52, A4.width - MARGIN, MARGIN + 430)
    page.draw_rect(frame, color=(0.48, 0.56, 0.48), fill=(0.945, 0.957, 0.941), width=1.2)
    inner = frame + (8, 8, -8, -8)
    left = pymupdf.Rect(inner.x0, inner.y0, inner.x0 + width * 0.49, inner.y1)
    right = pymupdf.Rect(inner.x0 + width * 0.51, inner.y0, inner.x1, inner.y1)
    page.insert_htmlbox(left, rubriques_html(RUBRIQUES_GAUCHE), css=CSS)
    page.insert_htmlbox(right, rubriques_html(RUBRIQUES_DROITE), css=CSS)

    # Filigrane en travers de la page : impossible de prendre ce document pour un vrai.
    center = pymupdf.Point(A4.width / 2 - 215, MARGIN + 360)
    page.insert_text(
        center, "SPÉCIMEN - DOCUMENT FICTIF", fontsize=34, fontname="hebo", color=(0.75, 0.1, 0.1),
        fill_opacity=0.18, morph=(center, pymupdf.Matrix(-28)),
    )

    footer = pymupdf.Rect(MARGIN, A4.height - MARGIN - 16, A4.width - MARGIN, A4.height - MARGIN + 4)
    page.insert_htmlbox(footer, f'<p class="fictif">{FICTIF}</p>', css=CSS)
    doc.set_metadata({"title": "Carte grise (fictive)", "author": "Générateur de test"})
    data = doc.tobytes(garbage=3, deflate=True)
    path.write_bytes(data)
    return data


def build_photo(pdf_name: str, path: Path) -> None:
    """Photo de travers du document posé sur une table (même rendu que generer_photos.py)."""
    from generer_photos import page_image, perspective

    photo = perspective(
        page_image(pdf_name, dpi=150), (1500, 2000),
        [(150, 160), (1340, 110), (1380, 1880), (120, 1930)],
    )
    photo.save(path, quality=90)


def main() -> None:
    """Génère le PDF puis, si OpenCV est disponible, sa photo."""
    build_pdf(OUT / "carte-grise-fictive.pdf")
    print("carte-grise-fictive.pdf")
    try:
        build_photo("carte-grise-fictive.pdf", OUT / "photo-carte-grise-fictive.jpg")
        print("photo-carte-grise-fictive.jpg")
    except ImportError as exc:
        print(f"Photo non générée ({exc}) : ajouter --with opencv-python-headless")


if __name__ == "__main__":
    main()
