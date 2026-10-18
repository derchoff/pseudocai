"""Contrats et données du pipeline : agents attendus (Protocol), services injectés, entrée et
résultat. Aucun traitement ici."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..classification import Classification
from ..doctypes import DocTypeCatalog
from ..extraction import OcrEngine
from ..fields import FieldCatalog
from ..models import DocumentContent, Entity, Line, Span
from ..pseudonymizer import MappingRecord, Pseudonymizer, ValueCipher
from ..surrogates import SurrogateFactory


class Detector(Protocol):
    """Agent de détection des données personnelles."""
    prompt_sha256: str | None

    def detect(self, lines: list[Line]) -> list[Entity]:
        """Entités trouvées dans les lignes du document."""
        ...


class Verifier(Protocol):
    """Agent de vérification du texte pseudonymisé."""
    prompt_sha256: str | None

    def verify(self, pseudonymised_lines: list[tuple[str, str]]) -> list[Entity]:
        """Données restées en clair dans les lignes pseudonymisées (id, texte)."""
        ...


class Classifier(Protocol):
    """Agent de classification du type de document."""
    prompt_sha256: str | None

    def classify(self, pseudonymised_text: str) -> tuple[list[dict], DocTypeCatalog, str]:
        """Types probables du document : (types bruts, référentiel, empreinte du prompt)."""
        ...


class SpanDetector(Protocol):
    """Détecteur qui renvoie directement des zones (ex. Azure AI Language)."""

    def detect(self, lines: list[Line]) -> list[Span]:
        """Zones détectées dans les lignes du document."""
        ...


@dataclass
class Services:
    """Services utilisés par le pipeline : agents IA, OCR, jetons, chiffrement, valeurs fictives."""
    detector: Detector
    pseudonymizer: Pseudonymizer
    cipher: ValueCipher
    ocr: OcrEngine | None = None
    verifier: Verifier | None = None
    language: SpanDetector | None = None
    # Catalogue des champs (référentiel Markdown) : fournit la stratégie de normalisation par type.
    fields: Callable[[], FieldCatalog] | None = None
    # Agent de classification du type de document (None = classification désactivée).
    classifier: Classifier | None = None
    # Fabrique de valeurs fictives (obligatoire en mode PSEUDO_OUTPUT_MODE=substitution).
    surrogates: SurrogateFactory | None = None


Renderer = Callable[[bytes, DocumentContent, list[Span], int], bytes]


@dataclass
class PipelineInput:
    """Document à traiter. Le pipeline ne connaît que des PDF ; une image arrive ici déjà emballée
    en PDF par l'étape de préparation, avec son OCR et son rendu dédiés (voir prepared_image.py)."""

    pdf: bytes  # PDF analysé (PDF d'origine, ou image d'origine emballée en PDF)
    ocr: OcrEngine | None = None  # remplace services.ocr (ex. OCR de la copie redressée d'une image)
    force_ocr: bool = False  # True pour une image : toutes les pages sont des « scans »
    renderer: Renderer | None = None  # produit le document de sortie (défaut : PDF caviardé)
    source: dict = field(default_factory=dict)  # origine et préparation, ajoutées au rapport
    # Texte déjà extrait (texte d'un mail, JSON) : suite TEXT, ni PDF, ni OCR, ni rendu ; l'appelant
    # produit la sortie à partir de PipelineResult.spans.
    content: DocumentContent | None = None


@dataclass
class PipelineResult:
    """Résultat du pipeline : statut, motifs, document produit, correspondances, rapport et occurrences."""
    status: str  # "ok" | "quarantine"
    reasons: list[str] = field(default_factory=list)
    output: bytes | None = None  # document pseudonymisé, au format produit par le renderer
    text: str | None = None
    records: list[MappingRecord] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    classification: Classification | None = None
    # Une entrée par zone remplacée : jeton, type, valeur fictive, page, position (sans valeur d'origine).
    occurrences: list[dict] = field(default_factory=list)
    token_text: str | None = None  # texte pseudonymisé avec les jetons (sert au classement d'un ensemble)
    content: DocumentContent | None = None  # texte analysé
    spans: list[Span] = field(default_factory=list)  # zones remplacées, valeurs fictives comprises
