"""Consommation des services facturés à l'usage, relevée à chaque traitement (rapport « consommation »).

    {"pages_ocr": 3,                                   Document Intelligence : pages analysées
     "language": {"enregistrements": 2, "caracteres": 1450},   Azure AI Language (PII)
     "ia": {"detection":      {"appels": 1, "jetons_entree": …, "jetons_sortie": …, "jetons_raisonnement": …},
            "verification":   {…, somme des tours},
            "classification": {…}}}

Relevée toujours (pas seulement en mode diagnostic) : elle alimente les fiches de statistiques
(statistics/statistics.py), qui permettent de rapporter le coût au nombre de pages traitées.
Les jetons de sortie comprennent déjà les jetons de raisonnement (facturés comme de la sortie).
"""

from __future__ import annotations

AGENTS = ("detection", "verification", "classification")
AGENT_KEYS = ("appels", "jetons_entree", "jetons_sortie", "jetons_raisonnement")
LANGUAGE_RECORD_CHARS = 1000  # Azure AI Language facture par enregistrement de 1 000 caractères


def empty_agent() -> dict:
    """Consommation nulle d'un agent IA."""
    return {k: 0 for k in AGENT_KEYS}


def empty_consumption() -> dict:
    """Consommation nulle d'un traitement."""
    return {
        "pages_ocr": 0,
        "language": {"enregistrements": 0, "caracteres": 0},
        "ia": {agent: empty_agent() for agent in AGENTS},
    }


def agent_usage(agent) -> dict:
    """Appels et jetons du dernier appel de l'agent (statistiques last_stats), zéro sans statistiques."""
    stats = getattr(agent, "last_stats", None)
    if not isinstance(stats, dict):
        return empty_agent()
    return {k: int(stats.get(k, 0) or 0) for k in AGENT_KEYS}


def add_agent(total: dict, usage: dict) -> None:
    """Ajoute la consommation d'un appel d'agent au total."""
    for k in AGENT_KEYS:
        total[k] = total.get(k, 0) + int(usage.get(k, 0) or 0)


def merge(*items: dict | None) -> dict:
    """Somme de plusieurs consommations (texte d'un mail + classification du mail…)."""
    total = empty_consumption()
    for item in items:
        if not item:
            continue
        total["pages_ocr"] += int(item.get("pages_ocr", 0) or 0)
        for k in ("enregistrements", "caracteres"):
            total["language"][k] += int((item.get("language") or {}).get(k, 0) or 0)
        for agent, usage in (item.get("ia") or {}).items():
            add_agent(total["ia"].setdefault(agent, empty_agent()), usage)
    return total


def language_records(texts: list[str]) -> int:
    """Enregistrements facturés par Azure AI Language : un par tranche de 1 000 caractères de chaque texte."""
    return sum(max(1, -(-len(t) // LANGUAGE_RECORD_CHARS)) for t in texts if t)
