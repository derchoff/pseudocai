"""Rendu du document de sortie : PDF caviardé, ou rendu dédié (image au format d'origine)."""

from __future__ import annotations

from ...redaction import render_pdf
from ..context import PipelineContext, step


@step("rendu")
def render(ctx: PipelineContext) -> None:
    """Produit ctx.output avec le rendu dédié du document (image), sinon le PDF caviardé."""
    source = ctx.source
    renderer = source.renderer or render_pdf
    ctx.output = renderer(source.pdf, ctx.content, ctx.spans, ctx.settings.ocr_render_dpi)
