"""Statistiques de traitement : consommation de chaque traitement (usage.py) et fiches du container
Cosmos DB « statistiques » (statistics.py). Voir docs/statistiques.md."""

from .statistics import build_items, record_statistics

__all__ = ["build_items", "record_statistics"]
