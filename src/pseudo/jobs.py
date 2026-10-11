"""Messages échangés entre les étapes (files Service Bus).

    incoming/ ──événement──► routeur ──job──► file « pdf-jobs »  ──► pseudonymisation
                                     └─job──► file « image-jobs » ──► préparation image ──job──► « pdf-jobs »

Les fichiers ne sont jamais déplacés : le document reste dans incoming/ et seul le job avance.
Le job transporte le contexte : identifiant de corrélation, document d'origine, format, et pour
une image préparée l'emplacement des fichiers intermédiaires dans work/<job_id>/.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

ORIGIN_PDF = "pdf"
ORIGIN_IMAGE = "image"


@dataclass
class PreparedImage:
    """Fichiers produits par l'étape de préparation d'une image (dans work/<job_id>/)."""

    render_pdf: str  # image d'origine (cadrage conservé, orientation EXIF appliquée) emballée en PDF
    transforms: str  # JSON : géométrie de chaque page (dimensions, matrice original -> copie OCR)
    ocr_pdf: str | None = None  # copie redressée et améliorée pour l'OCR (None si désactivée)
    preparation: dict = field(default_factory=dict)  # rapport de préparation (sans donnée personnelle)

    def paths(self) -> list[str]:
        return [p for p in (self.render_pdf, self.transforms, self.ocr_pdf) if p]


@dataclass
class Job:
    job_id: str  # identifiant de corrélation, attribué par le routeur
    document_id: str  # empreinte stable du document d'origine (traces, retraitements)
    source_container: str
    source_name: str  # chemin du document dans incoming/
    source_format: str  # pdf, jpeg, png, tiff, webp, bmp, heif
    origin: str  # "pdf" | "image"
    received_at: str
    prepared: PreparedImage | None = None

    @classmethod
    def new(cls, source_url: str, container: str, name: str, source_format: str, origin: str) -> "Job":
        return cls(
            job_id=str(uuid.uuid4()),
            document_id=document_id(source_url),
            source_container=container,
            source_name=name,
            source_format=source_format,
            origin=origin,
            received_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, body: bytes | str) -> "Job":
        data = json.loads(body)
        prepared = data.pop("prepared", None)
        return cls(**data, prepared=PreparedImage(**prepared) if prepared else None)

    @property
    def work_prefix(self) -> str:
        return f"{self.job_id}/"


def document_id(source_url: str) -> str:
    """Identifiant pseudonyme et stable : le nom de fichier peut contenir un nom de personne,
    il n'apparaît jamais dans les journaux."""
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
