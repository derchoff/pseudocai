"""État d'un document pendant le pipeline, contrat des étapes et arrêt anticipé.

Une étape est une fonction (ou tout objet appelable) qui reçoit le contexte, lit ce que les étapes
précédentes y ont mis et le complète. Elle ne garde rien pour elle : les mêmes étapes servent à
plusieurs documents traités en même temps (les Functions traitent plusieurs messages en parallèle).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..classification import Classification
from ..config import Settings
from ..fields import FieldCatalog
from ..models import DocumentContent, Line, Span
from ..pseudonymizer import MappingRecord
from .models import PipelineInput, Services


@dataclass
class PipelineContext:
    """Tout ce que le pipeline sait d'UN document, rempli au fil des étapes."""

    source: PipelineInput
    settings: Settings
    services: Services
    report: dict
    consumption: dict  # = report["consommation"] : pages OCR, Language, jetons des agents
    diag: dict | None  # = report["diagnostic"] en mode REPORT_DETAIL=diagnostic, sinon None
    started: float = field(default_factory=time.monotonic)
    reasons: list[str] = field(default_factory=list)  # motifs de quarantaine relevés en chemin

    # Lecture du document
    content: DocumentContent | None = None
    lines: list[Line] = field(default_factory=list)
    paged: bool = False  # True pour un PDF ou une image lus page par page (pas un texte déjà extrait)

    # Détection et vérification
    catalog: FieldCatalog | None = None  # référentiel des champs (normalisation, exceptions, valeurs fictives)
    spans: list[Span] = field(default_factory=list)  # zones à remplacer, avec leur jeton
    unresolved: list = field(default_factory=list)  # entités signalées mais introuvables dans le texte
    rounds_done: int = 0

    # Résultats
    records: list[MappingRecord] = field(default_factory=list)
    token_text: str | None = None  # texte avec les jetons (vérification, classement)
    text: str | None = None  # texte publié (valeurs fictives ou jetons)
    classification: Classification | None = None
    occurrences: list[dict] = field(default_factory=list)
    output: bytes | None = None  # document produit par le rendu


class Step(Protocol):
    """Étape du pipeline : complète le contexte, ou lève Rejected pour arrêter le document.
    name : nom de l'étape dans le rapport (durée dans diagnostic.durees_s)."""

    name: str

    def __call__(self, ctx: PipelineContext, /) -> None:
        """Exécute l'étape sur le document du contexte."""
        ...


def step(name: str) -> Callable[[Callable[[PipelineContext], None]], Step]:
    """Déclare une fonction comme étape du pipeline, sous ce nom :

        @step("detection")
        def detect(ctx: PipelineContext) -> None: ...
    """

    def declare(fn: Callable[[PipelineContext], None]) -> Step:
        """Pose le nom de l'étape sur la fonction."""
        fn.name = name  # type: ignore[attr-defined]
        return fn  # type: ignore[return-value]

    return declare


class Rejected(Exception):
    """Arrêt anticipé : le document part en quarantaine avec ce motif (PDF chiffré, écriture
    manuscrite…). extra : informations ajoutées au rapport de quarantaine seulement."""

    def __init__(self, reason: str, extra: dict | None = None):
        """reason : motif de quarantaine ; extra : clés ajoutées au rapport rendu."""
        super().__init__(reason)
        self.reason = reason
        self.extra = extra or {}
