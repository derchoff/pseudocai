"""Contrôle qualité par l'agent vérificateur : il relit le texte pseudonymisé, tour après tour,
tant qu'il trouve des données restées en clair (au plus VERIFICATION_ROUNDS tours)."""

from __future__ import annotations

import time

from ...exceptions import apply_exceptions
from ...models import DocumentContent, Entity, Line, PageContent, Span
from ...spans import coverage_signature, merge_spans, propagate, pseudonymise_document, resolve_entities
from ...statistics.usage import add_agent, agent_usage
from ..context import PipelineContext
from ..diagnostic import round_diagnostic
from .tokens import assign_tokens


def verify(ctx: PipelineContext) -> None:
    """Complète ctx.spans avec les résidus trouvés ; motif residus_apres_verification si le dernier
    tour trouve encore du nouveau. Sans vérificateur, aucun tour."""
    settings, services, content, catalog, diag = ctx.settings, ctx.services, ctx.content, ctx.catalog, ctx.diag
    exceptions = catalog.exceptions if catalog is not None else None
    still_finding = False
    step = time.monotonic()
    if services.verifier is not None:
        rounds: list[dict] = []
        for _ in range(settings.verification_rounds):
            ctx.rounds_done += 1
            round_started = time.monotonic()
            pseudo_lines = pseudonymise_document(content, ctx.spans)
            residual_entities = services.verifier.verify([(pl.id, pl.text) for pl in pseudo_lines])
            add_agent(ctx.consumption["ia"]["verification"], agent_usage(services.verifier))
            verifier_seconds = time.monotonic() - round_started
            residual_located: list[dict] | None = [] if diag is not None else None
            residual_spans, residual_unresolved = residuals_to_original(
                residual_entities, pseudo_lines, content, settings.fuzzy_min_score, residual_located
            )
            ctx.unresolved += residual_unresolved
            before = coverage_signature(ctx.spans)
            candidate = merge_spans(ctx.spans + residual_spans, content)
            candidate = merge_spans(candidate + propagate(candidate, content, settings.propagation_min_length), content)
            candidate = apply_exceptions(candidate, content, exceptions)
            after = coverage_signature(candidate)
            still_finding = after != before
            if diag is not None:
                rounds.append(
                    round_diagnostic(
                        ctx.rounds_done, services.verifier, residual_entities, residual_located,
                        before, after, candidate, verifier_seconds,
                    )
                )
            if not still_finding:
                break
            ctx.spans = candidate
            assign_tokens(ctx.spans, content, services.pseudonymizer, catalog)
        ctx.report["prompt_verification_sha256"] = services.verifier.prompt_sha256
        if still_finding:
            ctx.reasons.append("residus_apres_verification")
        if diag is not None:
            diag["verification"] = rounds
            diag["durees_s"]["verification"] = round(time.monotonic() - step, 2)
    ctx.report["tours_verification"] = ctx.rounds_done


def residuals_to_original(
    entities: list[Entity],
    pseudo_lines,
    content: DocumentContent,
    fuzzy_min_score: float,
    diagnostics: list[dict] | None = None,
) -> tuple[list[Span], list[Entity]]:
    """Les résidus sont localisés dans le texte pseudonymisé puis ramenés au texte d'origine."""
    by_id = {pl.id: pl for pl in pseudo_lines}
    pseudo_doc = DocumentContent(
        pages=[
            PageContent(
                index=page.index,
                source=page.source,
                lines=[Line(id=ln.id, page=ln.page, words=[], text=by_id[ln.id].text) for ln in page.lines],
            )
            for page in content.pages
        ]
    )
    pseudo_spans, unresolved = resolve_entities(entities, pseudo_doc, fuzzy_min_score, diagnostics)
    spans = []
    for s in pseudo_spans:
        a, b = by_id[s.line_id].to_original(s.start, s.end)
        if a < b:
            spans.append(Span(s.line_id, a, b, s.type, "verifier"))
    return spans, unresolved
