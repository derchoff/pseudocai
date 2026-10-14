"""Génère une note d'honoraires d'ostéopathe FICTIVE, remplie « à la main », pour tester le manuscrit.

Le formulaire (en-tête du cabinet, libellés) est imprimé ; les valeurs (patient, date de
naissance, adresse, somme, date) sont écrites avec une police manuscrite, avec de petites
irrégularités (taille, hauteur, inclinaison) et à l'encre bleue ; un cachet et une signature
complètent le document, rendu comme un scan légèrement penché. Toutes les données sont inventées
(téléphone dans les tranches réservées à la fiction, e-mail en example.com, ADELI et SIRET inventés).

Polices manuscrites : celles de Windows (Ink Free, Segoe Print, Mistral…), lues sur le poste au
moment de la génération ; elles ne sont pas incluses dans le dépôt.

Fichiers produits :
- facture-osteopathe-manuscrite.jpg      : « scan » JPEG
- facture-osteopathe-manuscrite-scan.pdf : même image dans un PDF sans couche texte

Usage (depuis la racine du projet) :
    uv run --python 3.12 --with pymupdf --with pillow python exemples/generer_facture_manuscrite.py
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent
FONTS = Path("C:/Windows/Fonts")
HANDWRITING_FONTS = ["Inkfree.ttf", "segoepr.ttf", "BRADHITC.TTF", "LHANDW.TTF"]
SIGNATURE_FONTS = ["MISTRAL.TTF", "FREESCPT.TTF", "segoesc.ttf"]

WIDTH, HEIGHT = 1240, 1754  # A4 à 150 dpi
PAPER = (250, 248, 242)
PRINT = (35, 35, 35)
INK = (24, 52, 140)  # encre bleue de stylo bille
STAMP = (88, 60, 150)  # encre de tampon violette

FICTIF = (
    "DOCUMENT FICTIF généré pour tester un service de pseudonymisation — "
    "toutes les personnes, coordonnées et numéros sont inventés."
)

# Valeurs « écrites à la main » (inventées).
PATIENT = "M. Thomas GARNIER"
NAISSANCE = "03/05/1979"
ADRESSE = "15 rue des Lilas, 35700 Rennes"
SOMME = "soixante euros (60 €)"
MOTIF = "séance d'ostéopathie (lombalgie)"
REGLEMENT = "chèque n° 4410287"
DATE = "07/10/2026"


def find_font(names: list[str]) -> Path:
    """Première police disponible parmi names (dossier des polices Windows)."""
    for name in names:
        if (FONTS / name).exists():
            return FONTS / name
    raise SystemExit(f"Aucune police manuscrite trouvée dans {FONTS} ({', '.join(names)})")


def printed_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Helvetica fournie par PyMuPDF (accents compris) pour les parties imprimées."""
    return ImageFont.truetype(io.BytesIO(pymupdf.Font("hebo" if bold else "helv").buffer), size)


def handwrite(page: Image.Image, xy: tuple[int, int], text: str, font_path: Path, size: int,
              rng: random.Random, color=INK, slope: float | None = None) -> int:
    """Écrit text « à la main » à partir de xy (haut gauche) ; renvoie la largeur occupée.

    Chaque caractère varie un peu en taille et en hauteur ; la ligne entière est légèrement
    inclinée, comme une écriture sur une ligne pointillée.
    """
    layer = Image.new("RGBA", (int(size * len(text) * 0.9) + 40, size * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x = 10
    for char in text:
        font = ImageFont.truetype(str(font_path), max(8, int(size * rng.uniform(0.92, 1.08))))
        draw.text((x, size * 0.4 + rng.uniform(-2.5, 2.5)), char, font=font, fill=color + (235,))
        x += font.getlength(char) * rng.uniform(0.96, 1.06)
    layer = layer.crop((0, 0, int(x) + 10, layer.height))
    layer = layer.rotate(slope if slope is not None else rng.uniform(-1.6, 1.0),
                         resample=Image.BICUBIC, expand=True)
    page.alpha_composite(layer, (xy[0] - 10, xy[1] - int(size * 0.4)))
    return int(x)


def dotted_line(draw: ImageDraw.ImageDraw, x0: int, x1: int, y: int) -> None:
    """Ligne pointillée imprimée à compléter à la main."""
    for x in range(x0, x1, 9):
        draw.line((x, y, x + 3, y), fill=(120, 120, 120), width=1)


def stamp(page: Image.Image, xy: tuple[int, int], rng: random.Random) -> None:
    """Cachet du praticien : cadre et texte violets, un peu penchés et irréguliers."""
    layer = Image.new("RGBA", (470, 165), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((4, 4, 464, 159), radius=10, outline=STAMP + (200,), width=4)
    lines = [
        ("Camille ROUSSEL", 30, True),
        ("Ostéopathe D.O. — ADELI 359300417", 21, False),
        ("8 place du Marché, 35000 Rennes", 21, False),
        ("Tél. 02 61 91 23 57", 21, False),
    ]
    y = 14
    for text, size, bold in lines:
        draw.text((22, y), text, font=printed_font(size, bold), fill=STAMP + (210,))
        y += size + 12
    # Encrage irrégulier du tampon : quelques zones plus pâles.
    alpha = layer.getchannel("A")
    holes = Image.new("L", layer.size, 255)
    hd = ImageDraw.Draw(holes)
    for _ in range(140):
        cx, cy = rng.randrange(layer.width), rng.randrange(layer.height)
        r = rng.randrange(2, 7)
        hd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rng.randrange(60, 160))
    layer.putalpha(Image.composite(alpha, Image.new("L", layer.size, 0), holes))
    page.alpha_composite(layer.rotate(rng.uniform(-6, -3), resample=Image.BICUBIC, expand=True), xy)


def build_page(seed: int = 7) -> Image.Image:
    """Note d'honoraires complète, avant l'effet « scan »."""
    rng = random.Random(seed)
    hand = find_font(HANDWRITING_FONTS)
    signature = find_font(SIGNATURE_FONTS)
    page = Image.new("RGBA", (WIDTH, HEIGHT), PAPER + (255,))
    draw = ImageDraw.Draw(page)

    # En-tête imprimé du cabinet.
    draw.text((90, 90), "Cabinet d'ostéopathie du Thabor", font=printed_font(40, True), fill=(30, 70, 110))
    draw.text((90, 145), "Camille ROUSSEL — Ostéopathe D.O.", font=printed_font(24), fill=PRINT)
    draw.text((90, 178), "8 place du Marché, 35000 Rennes — Tél. 02 61 91 23 57", font=printed_font(21), fill=PRINT)
    draw.text((90, 207), "c.roussel.osteo@example.com — ADELI 359300417 — SIRET 852 147 963 00018",
              font=printed_font(19), fill=(90, 90, 90))
    draw.line((90, 250, WIDTH - 90, 250), fill=(30, 70, 110), width=3)
    draw.text((90, 290), "NOTE D'HONORAIRES", font=printed_font(36, True), fill=PRINT)
    draw.text((WIDTH - 360, 300), "N° 2026-0412", font=printed_font(24), fill=PRINT)

    # Champs imprimés à compléter, remplis « à la main ».
    fields = [
        ("Reçu de :", PATIENT),
        ("Né(e) le :", NAISSANCE),
        ("Adresse :", ADRESSE),
        ("La somme de :", SOMME),
        ("Pour :", MOTIF),
        ("Règlement :", REGLEMENT),
    ]
    y = 400
    for label, value in fields:
        draw.text((90, y), label, font=printed_font(26), fill=PRINT)
        dotted_line(draw, 300, WIDTH - 90, y + 34)
        handwrite(page, (315, y - 6), value, hand, 40, rng)
        y += 95

    draw.text((90, y + 20), "Fait à Rennes, le", font=printed_font(26), fill=PRINT)
    dotted_line(draw, 300, 640, y + 54)
    handwrite(page, (315, y + 14), DATE, hand, 40, rng)

    # Mention et signature manuscrites, cachet.
    handwrite(page, (90, y + 130), "Acquittée", hand, 46, rng, slope=-4)
    draw.text((WIDTH - 470, y + 110), "Signature et cachet :", font=printed_font(24), fill=PRINT)
    stamp(page, (WIDTH - 560, y + 160), rng)
    handwrite(page, (WIDTH - 430, y + 250), "C. Roussel", signature, 64, rng, slope=8)

    draw.text((90, HEIGHT - 150), "Les séances d'ostéopathie ne sont pas prises en charge par l'Assurance Maladie.",
              font=printed_font(19), fill=(90, 90, 90))
    draw.text((90, HEIGHT - 90), FICTIF, font=printed_font(16), fill=(170, 0, 0))
    return page


def scan_effect(page: Image.Image, seed: int = 11) -> Image.Image:
    """Effet de numérisation : légère rotation, flou, grain, contraste un peu écrasé."""
    rng = random.Random(seed)
    img = page.convert("RGB").rotate(-1.3, resample=Image.BICUBIC, expand=False, fillcolor=(236, 236, 232))
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    px = img.load()
    for _ in range(img.width * img.height // 70):
        x, y = rng.randrange(img.width), rng.randrange(img.height)
        d = rng.randrange(25, 70)
        r, g, b = px[x, y]
        px[x, y] = (max(0, r - d), max(0, g - d), max(0, b - d))
    return img


def main() -> None:
    """Génère le JPEG « scanné » et sa version PDF sans couche texte."""
    img = scan_effect(build_page())
    jpg = OUT / "facture-osteopathe-manuscrite.jpg"
    img.save(jpg, quality=88)
    print(jpg.name)

    doc = pymupdf.open()
    a4 = pymupdf.paper_rect("a4")
    page = doc.new_page(width=a4.width, height=a4.height)
    page.insert_image(a4, stream=jpg.read_bytes())
    pdf = OUT / "facture-osteopathe-manuscrite-scan.pdf"
    pdf.write_bytes(doc.tobytes(deflate=True))
    print(pdf.name)


if __name__ == "__main__":
    main()
