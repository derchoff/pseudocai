"""Orchestration de bout en bout pour un PDF (indépendante d'Azure Functions, donc testable)."""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Protocol

import pymupdf

from .config import Settings
from .detection.regex_rules import detect_regex
from .extraction import OcrEngine, extract_document
from .fields import FieldCatalog
from .models import DocumentContent, Entity, Line, PageContent, Span
from .pseudonymizer import MappingRecord, Pseudonymizer, ValueCipher
from .redaction import normalize_rotation, render_pdf
from .spans import (
    coverage_signature,
    merge_spans,
    propagate,
    pseudonymise_document,
    render_text,
    resolve_entities,
)

log = logging.getLogger(__name__)


class Detector(Protocol):
    prompt_sha256: str | None

    def detect(self, lines: list[Line]) -> list[Entity]:
        ...


class Verifier(Protocol):
    prompt_sha256: str | None

    def verify(self, pseudonymised_lines: list[tuple[str, str]]) -> list[Entity]:
        ...


class SpanDetector(Protocol):
    def detect(self, lines: list[Line]) -> list[Span]:
        ...


@dataclass
class Services:
    detector: Detector
    pseudonymizer: Pseudonymizer
    cipher: ValueCipher
    ocr: OcrEngine | None = None
    verifier: Verifier | None = None
    language: SpanDetector | None = None
    # Catalogue des champs (référentiel Markdown) : fournit la stratégie de normalisation par type.
    fields: Callable[[], FieldCatalog] | None = None


@dataclass
class PipelineResult:
    status: str  # "ok" | "quarantine"
    reasons: list[str] = field(default_factory=list)
    pdf: bytes | None = None
    text: str | None = None
    records: list[MappingRecord] = field(default_factory=list)
    report: dict = field(default_factory=dict)


def run_pipeline(pdf_bytes: bytes, settings: Settings, services: Services) -> PipelineResult:
    started = time.monotonic()
    report: dict = {"pipeline_version": "0.1.0"}
    reasons: list[str] = []

    # 1. Ouverture et contrôles préalables -------------------------------------------------
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 - tout PDF illisible part en quarantaine
        return PipelineResult(status="quarantine", reasons=["pdf_illisible"], report=report)
    if doc.needs_pass:
        return PipelineResult(status="quarantine", reasons=["pdf_chiffre"], report=report)
    if doc.page_count == 0:
        return PipelineResult(status="quarantine", reasons=["pdf_vide"], report=report)
    if doc.page_count > settings.max_pages:
        return PipelineResult(status="quarantine", reasons=["trop_de_pages"], report={"pages": doc.page_count})
    if hasattr(doc, "bake"):
        # Valeurs de formulaires et annotations deviennent du contenu de page : elles sont
        # ainsi extraites, détectées et caviardées comme le reste.
        doc.bake(annots=True, widgets=True)
    normalize_rotation(doc)

    # 2. Extraction du texte positionné ----------------------------------------------------
    pages = extract_document(
        pdf_bytes,
        doc,
        services.ocr,
        min_native_chars=settings.min_native_chars,
        image_coverage_threshold=settings.image_coverage_ocr_threshold,
        force_ocr=settings.force_ocr,
        ocr_native_pages=settings.ocr_native_pages,
    )
    doc.close()
    content = DocumentContent(pages=pages)
    lines = content.lines()
    ocr_pages = [p for p in pages if p.source == "ocr"]
    report.update(
        pages=len(pages),
        pages_natives=len(pages) - len(ocr_pages),
        pages_ocr=len(ocr_pages),
        lignes=len(lines),
        lignes_texte_images=sum(
            1 for p in pages if p.source == "native" for ln in p.lines if ln.source == "ocr"
        ),
    )
    low_conf = [
        p.index + 1
        for p in ocr_pages
        if p.ocr_confidence is not None and p.lines and p.ocr_confidence < settings.ocr_min_confidence
    ]
    if low_conf:
        reasons.append("ocr_faible_confiance")
        report["pages_ocr_faible_confiance"] = low_conf

    # 3. Détection (agent IA + filets de sécurité) -----------------------------------------
    catalog = services.fields() if services.fields else None
    if catalog is not None:
        report["champs"] = list(catalog.types)
    entities = services.detector.detect(lines)
    spans, unresolved = resolve_entities(entities, content, settings.fuzzy_min_score)
    spans += detect_regex(lines)
    if services.language is not None:
        spans += services.language.detect(lines)
    spans = merge_spans(spans, content)
    spans = merge_spans(spans + propagate(spans, content, settings.propagation_min_length), content)
    _assign_tokens(spans, content, services.pseudonymizer, catalog)
    report["prompt_detection_sha256"] = services.detector.prompt_sha256

    # 4. Contrôle qualité par l'agent vérificateur -----------------------------------------
    rounds_done = 0
    still_finding = False
    if services.verifier is not None:
        for _ in range(settings.verification_rounds):
            rounds_done += 1
            pseudo_lines = pseudonymise_document(content, spans)
            residual_entities = services.verifier.verify([(pl.id, pl.text) for pl in pseudo_lines])
            residual_spans, residual_unresolved = _residuals_to_original(
                residual_entities, pseudo_lines, content, settings.fuzzy_min_score
            )
            unresolved += residual_unresolved
            before = coverage_signature(spans)
            candidate = merge_spans(spans + residual_spans, content)
            candidate = merge_spans(candidate + propagate(candidate, content, settings.propagation_min_length), content)
            still_finding = coverage_signature(candidate) != before
            if not still_finding:
                break
            spans = candidate
            _assign_tokens(spans, content, services.pseudonymizer, catalog)
        report["prompt_verification_sha256"] = services.verifier.prompt_sha256
        if still_finding:
            reasons.append("residus_apres_verification")
    report["tours_verification"] = rounds_done

    if unresolved:
        report["entites_non_localisees"] = len(unresolved)
        if settings.quarantine_on_unlocated:
            reasons.append("entites_non_localisees")

    # 5. Rendu --------------------------------------------------------------------------------
    report["entites_par_type"] = dict(Counter(s.type for s in spans))
    report["entites_par_source"] = dict(Counter(s.source for s in spans))
    records = _mapping_records(spans, content, services, catalog)
    report["jetons_distincts"] = len(records)
    text = render_text(content, pseudonymise_document(content, spans))
    pdf = render_pdf(pdf_bytes, content, spans, dpi=settings.ocr_render_dpi)
    report["duree_secondes"] = round(time.monotonic() - started, 2)

    status = "quarantine" if reasons else "ok"
    report["statut"] = status
    report["motifs"] = reasons
    return PipelineResult(status=status, reasons=reasons, pdf=pdf, text=text, records=records, report=report)


def _strategy(catalog: FieldCatalog | None, entity_type: str) -> str | None:
    return catalog.strategy(entity_type) if catalog is not None else None


def _assign_tokens(
    spans: list[Span], content: DocumentContent, pseudonymizer: Pseudonymizer, catalog: FieldCatalog | None
) -> None:
    for s in spans:
        line = content.line(s.line_id)
        s.token = pseudonymizer.token(s.type, s.pseudonym_value(line), _strategy(catalog, s.type))


def _residuals_to_original(
    entities: list[Entity], pseudo_lines, content: DocumentContent, fuzzy_min_score: float
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
    pseudo_spans, unresolved = resolve_entities(entities, pseudo_doc, fuzzy_min_score)
    spans = []
    for s in pseudo_spans:
        a, b = by_id[s.line_id].to_original(s.start, s.end)
        if a < b:
            spans.append(Span(s.line_id, a, b, s.type, "verifier"))
    return spans, unresolved


def _mapping_records(
    spans: list[Span], content: DocumentContent, services: Services, catalog: FieldCatalog | None
) -> list[MappingRecord]:
    records: dict[str, MappingRecord] = {}
    for s in spans:
        if s.token in records:
            continue
        value = s.pseudonym_value(content.line(s.line_id))
        records[s.token] = MappingRecord(
            token=s.token,
            type=s.type,
            fingerprint=services.pseudonymizer.fingerprint(s.type, value, _strategy(catalog, s.type)),
            ciphertext=services.cipher.encrypt(s.token, value),
        )
    return list(records.values())
