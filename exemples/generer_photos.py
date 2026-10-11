"""Génère des PHOTOS et SCANS FICTIFS des factures d'exemple, pour tester le support des images.

À lancer après generer_factures.py (depuis la racine du projet) :
    uv run --python 3.12 --with pymupdf --with pillow --with pillow-heif --with opencv-python-headless python exemples/generer_photos.py

Fichiers produits (toutes les données sont celles, inventées, des factures fictives) :
- photo-facture-dentiste.jpg       : photo en perspective sur une table, stockée « couchée » avec
                                     orientation EXIF 6 et marque/modèle d'appareil (doivent disparaître)
- photo-facture-optique.heic       : photo d'iPhone (HEIC), légère perspective
- scan-facture-psychologue.png     : scan à plat, légèrement penché (2°)
- scan-factures-multipage.tiff     : TIFF de 2 pages (dentiste + kiné)
- photo-facture-kine-floue.jpg     : photo floue -> doit partir en quarantaine (image_floue)
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageFilter

OUT = Path(__file__).resolve().parent
TABLE_RGB = (52, 44, 38)


def page_image(pdf_name: str, dpi: int = 150) -> Image.Image:
    page = pymupdf.open(OUT / pdf_name)[0]
    return Image.open(io.BytesIO(page.get_pixmap(dpi=dpi).tobytes("png"))).convert("RGB")


def perspective(img: Image.Image, canvas: tuple[int, int], quad) -> Image.Image:
    w, h = img.size
    src = np.float32([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]])
    matrix = cv2.getPerspectiveTransform(src, np.float32(quad))
    warped = cv2.warpPerspective(np.asarray(img), matrix, canvas, borderValue=TABLE_RGB)
    noise = np.random.default_rng(7).normal(0, 4, warped.shape)
    return Image.fromarray(np.clip(warped + noise, 0, 255).astype(np.uint8))


def main() -> None:
    # 1. Photo en perspective, stockée couchée avec orientation EXIF et métadonnées d'appareil.
    dentiste = perspective(
        page_image("facture-dentiste.pdf"), (1500, 2000),
        [(170, 140), (1360, 200), (1310, 1890), (110, 1830)],
    )
    exif = Image.Exif()
    exif[0x0112] = 6  # « tourner de 90° pour afficher »
    exif[0x010F], exif[0x0110] = "FictivePhone", "FP-12 Pro"
    dentiste.rotate(90, expand=True).save(OUT / "photo-facture-dentiste.jpg", quality=90, exif=exif)

    # 2. Photo HEIC (format par défaut des iPhone).
    optique = perspective(
        page_image("facture-optique.pdf"), (1400, 1900),
        [(90, 80), (1310, 110), (1290, 1820), (70, 1800)],
    )
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        optique.save(OUT / "photo-facture-optique.heic", format="HEIF", quality=85)
    except (ImportError, KeyError, OSError) as exc:
        print(f"HEIC non généré ({exc}) : photo-facture-optique.jpg à la place")
        optique.save(OUT / "photo-facture-optique.jpg", quality=90)

    # 3. Scan à plat légèrement penché.
    psy = page_image("facture-psychologue.pdf", dpi=200)
    psy.rotate(-2, expand=True, fillcolor="white", resample=Image.BICUBIC).save(OUT / "scan-facture-psychologue.png")

    # 4. TIFF multipage.
    pages = [page_image("facture-dentiste.pdf"), page_image("facture-kinesitherapeute.pdf")]
    pages[0].save(OUT / "scan-factures-multipage.tiff", save_all=True, append_images=pages[1:], compression="tiff_deflate")

    # 5. Photo floue (doit partir en quarantaine).
    kine = perspective(
        page_image("facture-kinesitherapeute.pdf"), (1500, 2000),
        [(150, 120), (1350, 180), (1300, 1880), (120, 1820)],
    )
    kine.filter(ImageFilter.GaussianBlur(6)).save(OUT / "photo-facture-kine-floue.jpg", quality=88)

    for name in sorted(p.name for p in OUT.iterdir() if p.name.startswith(("photo-", "scan-"))):
        print(name)


if __name__ == "__main__":
    main()
