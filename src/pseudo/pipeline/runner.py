"""Orchestration de bout en bout pour un PDF, une image préparée ou un texte déjà extrait (texte d'un
mail, JSON de métadonnées). Indépendante d'Azure Functions, donc testable.

run_pipeline enchaîne les étapes (steps/) sur un contexte (context.py), selon l'une des suites :

    DOCUMENT  PDF ou image seuls :  lecture ─► détection ─► vérification ─► bilan ─► classification
                                    ─► valeurs fictives ─► rendu
    PART      pièce jointe :        la même, sans classification (le mail est classé en entier)
    TEXT      texte déjà extrait :  détection ─► vérification ─► bilan ─► valeurs fictives
              (texte d'un mail, JSON de métadonnées ; l'appelant produit la sortie)

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
from .steps import (
    build_records,
    classify,
    detect,
    not_classified,
    read_document,
    render,
    substitute,
    use_given_content,
    verify,
)

DOCUMENT: tuple[Step, ...] = (read_document, detect, verify, build_records, classify, substitute, render)
PART: tuple[Step, ...] = (read_document, detect, verify, build_records, not_classified, substitute, render)
TEXT: tuple[Step, ...] = (use_given_content, detect, verify, build_records, not_classified, substitute)


def steps_for(source: PipelineInput) -> tuple[Step, ...]:
    """Suite par défaut : TEXT pour un texte déjà extrait (source.content), sinon DOCUMENT."""
    return TEXT if source.content is not None else DOCUMENT


def run_pipeline(
    source: PipelineInput | bytes,
    settings: Settings,
    services: Services,
    steps: tuple[Step, ...] | None = None,
) -> PipelineResult:
    """Pseudonymise un document de bout en bout.

    Contrôles, extraction (texte et OCR), détection, jetons, vérification, classification, rendu.
    Ne touche à aucun stockage : la publication est faite par l'appelant. steps : DOCUMENT, PART,
    TEXT, ou toute autre suite d'étapes (défaut : steps_for(source)).
    """
    if isinstance(source, bytes):
        source = PipelineInput(pdf=source)
    ctx = _new_context(source, settings, services)
    try:
        for step in steps or steps_for(source):
            started = time.monotonic()
            try:
                step(ctx)
            finally:  # durée notée même si l'étape arrête le document
                if ctx.diag is not None:
                    ctx.diag["durees_s"][_name(step)] = round(time.monotonic() - started, 2)
    except Rejected as rejected:
        return _rejected(ctx, rejected)
    return _result(ctx)


def _name(step: Step) -> str:
    """Nom de l'étape : celui déclaré avec @step, sinon celui de la fonction ou de la classe."""
    return getattr(step, "name", None) or getattr(step, "__name__", None) or type(step).__name__


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
