"""Étapes du pipeline, dans l'ordre d'un document : lecture, détection, vérification, bilan
(correspondances, texte avec jetons), classification, valeurs fictives, rendu.

Chaque étape est une fonction `etape(ctx: PipelineContext) -> None`, déclarée avec @step(nom) : le
nom sert de clé à sa durée (diagnostic.durees_s), mesurée par le runner (voir context.py)."""

from .classify import classify, classify_text, not_classified
from .detect import detect
from .read import read_document, use_given_content
from .records import build_records
from .render import render
from .surrogates import substitute
from .verify import verify

__all__ = [
    "build_records",
    "classify",
    "classify_text",
    "detect",
    "not_classified",
    "read_document",
    "render",
    "substitute",
    "use_given_content",
    "verify",
]
