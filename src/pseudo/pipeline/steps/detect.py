"""Détection des données personnelles : agent IA, puis filets de sécurité (règles, Azure AI
Language), fusion des zones, propagation aux autres occurrences, exceptions du référentiel, jetons."""

from __future__ import annotations

import time

from ...detection.regex_rules import detect_regex
from ...exceptions import apply_exceptions
from ...models import Span
from ...spans import merge_spans, propagate, resolve_entities
from ...statistics.usage import agent_usage
from ..context import PipelineContext
from ..diagnostic import agent_stats, localisation_diagnostic
from .tokens import assign_tokens


def detect(ctx: PipelineContext) -> None:
    """Zones à remplacer, avec leur jeton (ctx.spans), et entités introuvables (ctx.unresolved)."""
    settings, services, content, lines, report, diag = (
        ctx.settings, ctx.services, ctx.content, ctx.lines, ctx.report, ctx.diag
    )
    catalog = ctx.catalog = services.fields() if services.fields else None
    if catalog is not None:
        report["champs"] = list(catalog.types)
    step = time.monotonic()
    entities = services.detector.detect(lines)
    ctx.consumption["ia"]["detection"] = agent_usage(services.detector)
    if ctx.source.content is None:
        # Pages d'un vrai document (PDF, image) dont le texte part aux agents IA : celles qui ont au
        # moins une ligne non vide. Un texte déjà extrait (corps de mail, JSON) n'a pas de page.
        ctx.consumption["pages_ia"] = len({line.page for line in lines if line.text.strip()})
    llm_seconds = time.monotonic() - step
    located: list[dict] | None = [] if diag is not None else None
    spans, ctx.unresolved = resolve_entities(entities, content, settings.fuzzy_min_score, located)
    regex_spans = detect_regex(lines)
    spans += regex_spans
    language_spans: list[Span] | None = None
    language_seconds = 0.0
    if services.language is not None:
        language_started = time.monotonic()
        language_spans = services.language.detect(lines)
        language_stats = getattr(services.language, "last_stats", None) or {}
        for key in ("enregistrements", "caracteres"):
            ctx.consumption["language"][key] += int(language_stats.get(key, 0) or 0)
        language_seconds = time.monotonic() - language_started
        spans += language_spans
    before_merge = len(spans)
    spans = merge_spans(spans, content)
    propagated = propagate(spans, content, settings.propagation_min_length)
    spans = merge_spans(spans + propagated, content)
    exceptions = catalog.exceptions if catalog is not None else None
    before_exceptions = len(spans)
    spans = apply_exceptions(spans, content, exceptions)
    assign_tokens(spans, content, services.pseudonymizer, catalog)
    ctx.spans = spans
    report["prompt_detection_sha256"] = services.detector.prompt_sha256
    if diag is not None:
        diag["detection"] = {
            "llm": agent_stats(services.detector, entities, llm_seconds),
            "regex": len(regex_spans),
            "language": None if language_spans is None else len(language_spans),
            "propagation": len(propagated),
            "zones_avant_fusion": before_merge,
            "zones_retenues": len(spans),
            "zones_retirees_exceptions": before_exceptions - len(spans),
        }
        diag["localisation"] = localisation_diagnostic(located)
        diag["durees_s"]["detection_llm"] = round(llm_seconds, 2)
        if language_spans is not None:
            diag["durees_s"]["detection_language"] = round(language_seconds, 2)
        diag["durees_s"]["detection"] = round(time.monotonic() - step, 2)
