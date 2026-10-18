"""Orchestration de bout en bout pour un PDF, une image préparée ou un texte déjà extrait (texte d'un
mail, JSON de métadonnées). Indépendante d'Azure Functions, donc testable.

run_pipeline enchaîne les étapes (steps/) sur un contexte (context.py) :

    lecture ─► détection ─► vérification ─► bilan ─► classification ─► valeurs fictives ─► rendu
    (read_document, ou use_given_content pour un texte déjà extrait)

Dépendances entre étapes, à respecter si l'ordre change :
- les jetons sont posés par la détection puis recalculés à chaque tour de vérification ;
- build_records fixe le texte avec les jetons (token_text) APRÈS la vérification ; la classification
  lit ce texte-là, jamais le texte aux valeurs fictives ;
- les valeurs fictives (substitute) viennent après la vérification et avant le rendu : seul le
  document publié en reçoit ;
- une étape peut arrêter le document (Rejected) : il part en quarantaine, sans classification.
"""

from __future__ import annotations

import time

from ..classification import STATUT_NON_APPLICABLE, unclassified
from ..config import Settings
from ..statistics.usage import empty_consumption
from .context import PipelineContext, Rejected, Step
from .models import PipelineInput, PipelineResult, Services
from .steps import build_records, classify, detect, read_document, render, substitute, use_given_content, verify

# Étapes communes, après la lecture du document.
ANALYSIS: tuple[Step, ...] = (detect, verify, build_records, classify, substitute, render)


def steps_for(source: PipelineInput) -> tuple[Step, ...]:
    """Étapes d'un document : lecture du PDF, ou texte déjà extrait, puis les étapes communes."""
    return ((use_given_content if source.content is not None else read_document), *ANALYSIS)


def run_pipeline(
    source: PipelineInput | bytes,
    settings: Settings,
    services: Services,
    steps: tuple[Step, ...] | None = None,
) -> PipelineResult:
    """Pseudonymise un document de bout en bout.

    Contrôles, extraction (texte et OCR), détection, jetons, vérification, classification, rendu.
    Ne touche à aucun stockage : la publication est faite par l'appelant. steps : étapes à
    exécuter (défaut : steps_for(source)).
    """
    if isinstance(source, bytes):
        source = PipelineInput(pdf=source)
    ctx = _new_context(source, settings, services)
    try:
        for step in steps or steps_for(source):
            step(ctx)
    except Rejected as rejected:
        return _rejected(ctx, rejected)
    return _result(ctx)


def _new_context(source: PipelineInput, settings: Settings, services: Services) -> PipelineContext:
    """Contexte d'un nouveau document : rapport initial, consommation, diagnostic si demandé."""
    report: dict = {"pipeline_version": "0.2.0"}
    if source.source:
        report["source"] = source.source
    # Consommation des services facturés (OCR, agents IA, Language), même en cas de quarantaine.
    consumption = report["consommation"] = empty_consumption()
    # Rapport détaillé (REPORT_DETAIL=diagnostic) : ce que chaque étape a fait, jamais les valeurs.
    diag = None
    if settings.report_detail == "diagnostic":
        diag = report["diagnostic"] = {"durees_s": {}}
    return PipelineContext(source, settings, services, report, consumption, diag)


def _result(ctx: PipelineContext) -> PipelineResult:
    """Résultat d'un document allé au bout : statut ok, ou quarantaine si des motifs ont été relevés."""
    report = ctx.report
    report["duree_secondes"] = round(time.monotonic() - ctx.started, 2)
    status = "quarantine" if ctx.reasons else "ok"
    report["statut"] = status
    report["motifs"] = ctx.reasons
    return PipelineResult(
        status=status,
        reasons=ctx.reasons,
        output=ctx.output,
        text=ctx.text,
        records=ctx.records,
        report=report,
        classification=ctx.classification,
        occurrences=ctx.occurrences,
        token_text=ctx.token_text,
        content=ctx.content,
        spans=ctx.spans,
    )


def _rejected(ctx: PipelineContext, rejected: Rejected) -> PipelineResult:
    """Document arrêté avant la détection : quarantaine, sans classification."""
    classification = unclassified(STATUT_NON_APPLICABLE, ctx.settings.unclassified_folder)
    report = {
        **ctx.report,
        **rejected.extra,
        "statut": "quarantine",
        "motifs": [rejected.reason],
        "classification": classification.to_dict(),
    }
    return PipelineResult(status="quarantine", reasons=[rejected.reason], report=report, classification=classification)
