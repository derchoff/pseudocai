"""Décodage et encodage des images (Pillow + pillow-heif), partagés par l'étape de préparation
et par le rendu final. Aucune dépendance OpenCV ici.

- Décodage : JPEG, PNG, TIFF (multipage), WebP, BMP, HEIF/HEIC, uniquement dans le format annoncé
  par l'extension ; orientation EXIF appliquée ;
  conversion en RGB (transparence aplatie sur fond blanc) ; refus des images géantes.
- Encodage : même format que l'entrée, SANS métadonnées EXIF (le GPS d'une photo de smartphone
  est une donnée personnelle).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from .formats import BMP, HEIF, JPEG, PNG, TIFF, WEBP

_PIL_FORMATS = {JPEG: "JPEG", PNG: "PNG", TIFF: "TIFF", WEBP: "WEBP", BMP: "BMP", HEIF: "HEIF"}
EXIF_ORIENTATION = 0x0112


class ImageDecodeError(ValueError):
    """Image illisible ou format non pris en charge."""


class ImageTooLargeError(ValueError):
    """Image au-delà de IMAGE_MAX_PIXELS."""


@dataclass
class DecodedImage:
    """Image décodée : format, pages en RGB (orientation EXIF appliquée), orientation EXIF d'origine."""
    format: str
    frames: list  # list[PIL.Image.Image] en RGB, orientation EXIF appliquée
    exif_orientation: int | None


def _pil():
    """Module PIL.Image, avec le support HEIF/HEIC activé si pillow-heif est installé."""
    from PIL import Image

    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:  # pragma: no cover - HEIF indisponible : les autres formats restent lisibles
        pass
    return Image


def decode(data: bytes, fmt: str, max_pixels: int) -> DecodedImage:
    """Décode une image en n'acceptant que le format annoncé par son extension.

    Lève ImageDecodeError si le contenu ne correspond pas, ImageTooLargeError au-delà de max_pixels.
    """
    Image = _pil()
    from PIL import ImageOps, ImageSequence, UnidentifiedImageError

    try:
        # Seul le format annoncé par l'extension est accepté : un PNG renommé en .jpg est refusé.
        img = Image.open(io.BytesIO(data), formats=[_PIL_FORMATS[fmt]])
    except (UnidentifiedImageError, OSError, KeyError) as exc:
        raise ImageDecodeError(str(exc)) from exc
    width, height = img.size
    if width * height > max_pixels:
        raise ImageTooLargeError(f"{width}x{height}")
    orientation = img.getexif().get(EXIF_ORIENTATION)
    frames = []
    try:
        # Chaque page est copiée pendant l'itération : l'itérateur renvoie toujours le même objet,
        # simplement positionné sur la page suivante.
        sources = ImageSequence.Iterator(img) if fmt == TIFF else [img]
        for frame in sources:
            frame = ImageOps.exif_transpose(frame) if orientation else frame.copy()
            frames.append(_to_rgb(frame))
    except (OSError, ValueError) as exc:
        raise ImageDecodeError(str(exc)) from exc
    return DecodedImage(format=fmt, frames=frames, exif_orientation=orientation)


def encode(frames: list, fmt: str, quality: int = 90) -> bytes:
    """Encode au format demandé, sans EXIF ni autre métadonnée d'origine."""
    _pil()
    out = io.BytesIO()
    pil_format = _PIL_FORMATS[fmt]
    clean = [f.copy() for f in frames]
    for f in clean:
        f.info.clear()
    first = clean[0]
    if fmt == TIFF:
        first.save(out, format="TIFF", save_all=True, append_images=clean[1:], compression="tiff_deflate")
    elif fmt in (JPEG, WEBP, HEIF):
        first.save(out, format=pil_format, quality=quality)
    else:
        first.save(out, format=pil_format)
    return out.getvalue()


def _to_rgb(img):
    """Convertit en RGB ; la transparence est aplatie sur fond blanc."""
    if img.mode == "RGB":
        return img
    Image = _pil()
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")
