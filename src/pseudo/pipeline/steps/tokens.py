"""Calcul des jetons, commun à la détection et à la vérification (pas une étape)."""

from __future__ import annotations

from ...fields import FieldCatalog
from ...models import DocumentContent, Span
from ...pseudonymizer import Pseudonymizer


def strategy(catalog: FieldCatalog | None, entity_type: str) -> str | None:
    """Normalisation du type selon le référentiel, ou None sans référentiel."""
    return catalog.strategy(entity_type) if catalog is not None else None


def assign_tokens(
    spans: list[Span], content: DocumentContent, pseudonymizer: Pseudonymizer, catalog: FieldCatalog | None
) -> None:
    """Calcule le jeton de chaque zone à partir de sa valeur normalisée."""
    for s in spans:
        line = content.line(s.line_id)
        s.token = pseudonymizer.token(s.type, s.pseudonym_value(line), strategy(catalog, s.type))
