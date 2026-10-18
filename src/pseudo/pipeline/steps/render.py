"""Rendu du document de sortie : PDF caviardé, ou rendu dédié (image au format d'origine)."""

from __future__ import annotations

import time

from ...redaction import render_pdf
from ..context import PipelineContext


def render(ctx: PipelineContext) -> None:
    """Produit ctx.output ; un texte déjà extrait sans rendu dédié est rendu par l'appelant."""
    source = ctx.source
    if source.content is None or source.renderer is not None:
        step = time.monotonic()
        renderer = source.renderer or render_pdf
        ctx.output = renderer(source.pdf, ctx.content, ctx.spans, ctx.settings.ocr_render_dpi)
        if ctx.diag is not None:
            ctx.diag["durees_s"]["rendu"] = round(time.monotonic() - step, 2)
