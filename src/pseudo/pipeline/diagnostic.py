"""Rapport détaillé (REPORT_DETAIL=diagnostic) : ce que chaque étape a fait, jamais les valeurs."""

from __future__ import annotations

from collections import Counter

from ..models import Entity, PageContent, Span

MAX_DIAGNOSTIC_ITEMS = 200  # au-delà, les listes sont tronquées (le total reste indiqué)


def pages_diagnostic(pages: list[PageContent]) -> list[dict]:
    """Par page : mode (natif ou OCR), lignes, mots, confiance OCR, mots manuscrits."""
    out = []
    for p in pages:
        entry = {
            "page": p.index + 1,
            "source": p.source,
            "lignes": len(p.lines),
            "mots": sum(len(ln.words) for ln in p.lines),
        }
        if p.source == "native":
            entry["lignes_texte_images"] = sum(1 for ln in p.lines if ln.source == "ocr")
        if p.ocr_confidence is not None:
            entry["confiance_ocr"] = round(p.ocr_confidence, 3)
        handwritten = sum(1 for ln in p.lines for w in ln.words if w.handwritten)
        if handwritten:
            entry["mots_manuscrits"] = handwritten
        out.append(entry)
    return out


def agent_stats(agent, entities: list[Entity], seconds: float) -> dict:
    """Statistiques d'un agent IA (appels, jetons, entités rendues ou écartées), ou à défaut le nombre
    d'entités et la durée mesurée ici."""
    stats = getattr(agent, "last_stats", None)
    if isinstance(stats, dict):
        return {**stats, "entites_retenues": len(entities)}
    return {"entites_retenues": len(entities), "duree_s": round(seconds, 2)}


def localisation_diagnostic(located: list[dict]) -> dict:
    """Méthodes de localisation employées et détail des échecs (sans les valeurs)."""
    failures = [e for e in located if e["methode"] == "echec"]
    out: dict = {
        "methodes": dict(Counter(e["methode"] for e in located)),
        "echecs": failures[:MAX_DIAGNOSTIC_ITEMS],
    }
    if failures:
        out["echecs_par_type"] = dict(Counter(e["type"] for e in failures))
    if len(failures) > MAX_DIAGNOSTIC_ITEMS:
        out["echecs_tronques"] = len(failures) - MAX_DIAGNOSTIC_ITEMS
    return out


def round_diagnostic(
    number: int,
    verifier,
    residuals: list[Entity],
    located: list[dict],
    before: set,
    after: set,
    candidate: list[Span],
    seconds: float,
) -> dict:
    """Un tour de vérification : résidus signalés (type, ligne), localisation, ce qui a été ajouté."""
    added = after - before
    added_types = Counter(
        s.type for s in candidate if any((s.line_id, i) in added for i in range(s.start, s.end))
    )
    return {
        "tour": number,
        "agent": agent_stats(verifier, residuals, seconds),
        "residus_signales": len(residuals),
        "residus": [{"type": e.type, "ligne": e.line_id or None} for e in residuals][:MAX_DIAGNOSTIC_ITEMS],
        "residus_par_type": dict(Counter(e.type for e in residuals)),
        "localisation": localisation_diagnostic(located),
        "nouveaux_caracteres_masques": len(added),
        "nouvelles_zones_par_type": dict(added_types),
        "lignes_completees": sorted({line_id for line_id, _ in added})[:MAX_DIAGNOSTIC_ITEMS],
        "a_trouve_du_nouveau": after != before,
    }
