"""Bilan de la détection : entités introuvables, comptes par type et par source, correspondances
chiffrées (jeton -> valeur d'origine) et texte avec les jetons."""

from __future__ import annotations

from collections import Counter

from ...fields import FieldCatalog
from ...models import DocumentContent, Span
from ...pseudonymizer import MappingRecord
from ...spans import pseudonymise_document, render_text
from ..context import PipelineContext, step
from ..models import Services
from .tokens import strategy


@step("bilan")
def build_records(ctx: PipelineContext) -> None:
    """Remplit ctx.records et ctx.token_text (le texte publié en mode jetons)."""
    settings, report = ctx.settings, ctx.report
    if ctx.unresolved:
        report["entites_non_localisees"] = len(ctx.unresolved)
        if settings.quarantine_on_unlocated:
            ctx.reasons.append("entites_non_localisees")

    report["entites_par_type"] = dict(Counter(s.type for s in ctx.spans))
    report["entites_par_source"] = dict(Counter(s.source for s in ctx.spans))
    ctx.records = mapping_records(ctx.spans, ctx.content, ctx.services, ctx.catalog)
    report["jetons_distincts"] = len(ctx.records)
    ctx.token_text = ctx.text = render_text(ctx.content, pseudonymise_document(ctx.content, ctx.spans))


def mapping_records(
    spans: list[Span], content: DocumentContent, services: Services, catalog: FieldCatalog | None
) -> list[MappingRecord]:
    """Une correspondance chiffrée par jeton distinct (valeur d'origine chiffrée + empreinte)."""
    records: dict[str, MappingRecord] = {}
    for s in spans:
        if s.token in records:
            continue
        value = s.pseudonym_value(content.line(s.line_id))
        records[s.token] = MappingRecord(
            token=s.token,
            type=s.type,
            fingerprint=services.pseudonymizer.fingerprint(s.type, value, strategy(catalog, s.type)),
            ciphertext=services.cipher.encrypt(s.token, value),
        )
    return list(records.values())
