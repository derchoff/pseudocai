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
MAIL = "eml"
JSON = "json"  # métadonnées de la GED, à côté d'un document (<nom du document>.json)

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
    ".eml": MAIL,
    ".json": JSON,
}

CONTENT_TYPES = {
    PDF: "application/pdf",
    JPEG: "image/jpeg",
    PNG: "image/png",
    TIFF: "image/tiff",
    WEBP: "image/webp",
    BMP: "image/bmp",
    HEIF: "image/heif",
    MAIL: "message/rfc822",
    JSON: "application/json",
}

# Type MIME d'une pièce jointe sans nom de fichier (image intégrée au corps d'un mail) -> extension.
EXTENSION_FOR_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/heic": ".heic",
    "image/heif": ".heif",
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
