"""Valeurs fictives réalistes (mode PSEUDO_OUTPUT_MODE=substitution).

Les jetons restent utilisés en interne (vérification, classification) ; les valeurs fictives ne
sont produites qu'au moment du rendu du document publié. Elles sont fabriquées par du code (pas
par l'IA), à partir des listes de data/ : déterministes, sûres et au format de l'original.
"""

from .factory import GENERATORS, SurrogateFactory

__all__ = ["GENERATORS", "SurrogateFactory"]
