"""Format d'un fichier, déduit UNIQUEMENT de son extension.

Le contenu n'est pas inspecté pour « deviner » le format. Chaque lecteur n'accepte ensuite que
le format annoncé par l'extension : PyMuPDF en mode PDF, Pillow restreint au format attendu.
Un fichier dont le contenu ne correspond pas à son extension (image renommée en .pdf, PNG renommé
en .jpg…) échoue donc à la lecture et part en quarantaine (`pdf_illisible`, `image_illisible`) :
ce peut être une tentative de contournement, il n'est pas réinterprété.
"""

from __future__ import annotations

import posixpath

PDF = "pdf"
JPEG = "jpeg"
PNG = "png"
TIFF = "tiff"
WEBP = "webp"
BMP = "bmp"
HEIF = "heif"

IMAGE_FORMATS = (JPEG, PNG, TIFF, WEBP, BMP, HEIF)

EXTENSIONS = {
    ".pdf": PDF,
    ".jpg": JPEG,
    ".jpeg": JPEG,
    ".png": PNG,
    ".tif": TIFF,
    ".tiff": TIFF,
    ".webp": WEBP,
    ".bmp": BMP,
    ".heic": HEIF,
    ".heif": HEIF,
}

CONTENT_TYPES = {
    PDF: "application/pdf",
    JPEG: "image/jpeg",
    PNG: "image/png",
    TIFF: "image/tiff",
    WEBP: "image/webp",
    BMP: "image/bmp",
    HEIF: "image/heif",
}


def extension(name: str) -> str:
    """Extension en minuscules, point compris (".pdf"), ou "" si absente."""
    return posixpath.splitext(name)[1].lower()


def format_from_name(name: str) -> str | None:
    """Format annoncé par l'extension du fichier, ou None si l'extension n'est pas prise en charge."""
    return EXTENSIONS.get(extension(name))


def is_image(fmt: str | None) -> bool:
    """Vrai si le format est un format d'image pris en charge."""
    return fmt in IMAGE_FORMATS
