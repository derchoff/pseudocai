"""Lecture du document : ouverture et contrôles du PDF, extraction du texte positionné (couche
texte et OCR), écriture manuscrite ; ou texte déjà extrait fourni par l'appelant."""

from __future__ import annotations

import pymupdf

from ...extraction import extract_document
from ...models import DocumentContent
from ...redaction import normalize_rotation
from ..context import PipelineContext, Rejected, step
from ..diagnostic import pages_diagnostic


@step("extraction")
def read_document(ctx: PipelineContext) -> None:
    """Ouvre le PDF, le contrôle et en extrait le texte positionné (couche texte et OCR).

    Lève Rejected si le document est refusé avant la détection : illisible, chiffré, vide, trop de
    pages, ou écriture manuscrite avec HANDWRITING_POLICY=quarantaine (avant tout appel IA).
    """
    source, settings, report = ctx.source, ctx.settings, ctx.report
    pdf_bytes = source.pdf
    # Ouverture et contrôles préalables
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 - tout PDF illisible part en quarantaine
        raise Rejected("pdf_illisible") from None
    if not doc.is_pdf:
        # MuPDF reconnaît d'autres formats même en mode « pdf » (une image renommée en .pdf
        # s'ouvrirait) : un contenu non conforme à l'extension n'est jamais réinterprété.
        doc.close()
        raise Rejected("pdf_illisible")
    if doc.needs_pass:
        raise Rejected("pdf_chiffre")
    if doc.page_count == 0:
        raise Rejected("pdf_vide")
    if doc.page_count > settings.max_pages:
        raise Rejected("trop_de_pages", {"pages": doc.page_count})
    if hasattr(doc, "bake"):
        # Valeurs de formulaires et annotations deviennent du contenu de page : elles sont
        # ainsi extraites, détectées et caviardées comme le reste.
        doc.bake(annots=True, widgets=True)
    normalize_rotation(doc)

    # Extraction du texte positionné
    pages = extract_document(
        pdf_bytes,
        doc,
        source.ocr or ctx.services.ocr,
        min_native_chars=settings.min_native_chars,
        image_coverage_threshold=settings.image_coverage_ocr_threshold,
        force_ocr=settings.force_ocr or source.force_ocr,
        ocr_native_pages=settings.ocr_native_pages,
        usage=ctx.consumption,
    )
    doc.close()
    ctx.content = DocumentContent(pages=pages)
    ctx.lines = ctx.content.lines()
    ctx.paged = True
    if ctx.diag is not None:
        ctx.diag["extraction"] = pages_diagnostic(pages)
    ocr_pages = [p for p in pages if p.source == "ocr"]
    report.update(
        pages=len(pages),
        pages_natives=len(pages) - len(ocr_pages),
        pages_ocr=len(ocr_pages),
        lignes=len(ctx.lines),
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
        ctx.reasons.append("ocr_faible_confiance")
        report["pages_ocr_faible_confiance"] = low_conf

    # Écriture manuscrite : la lecture est trop incertaine (un nom illisible ne serait ni détecté
    # ni masqué). Avec HANDWRITING_POLICY=quarantaine, le document part en revue humaine AVANT
    # tout appel aux agents IA.
    handwritten = {
        p.index + 1: n
        for p in pages
        if (n := sum(1 for ln in p.lines if ln.source == "ocr" for w in ln.words if w.handwritten))
    }
    report["mots_manuscrits"] = sum(handwritten.values())
    report["politique_manuscrit"] = settings.handwriting_policy
    if handwritten:
        report["pages_manuscrites"] = sorted(handwritten)
        if settings.handwriting_policy == "quarantaine":
            raise Rejected("manuscrit_detecte")


@step("extraction")
def use_given_content(ctx: PipelineContext) -> None:
    """Texte déjà extrait par l'appelant (texte d'un mail, JSON de métadonnées) : ni PDF, ni OCR."""
    ctx.content = ctx.source.content
    ctx.lines = ctx.content.lines()
    ctx.report["lignes"] = len(ctx.lines)
    if ctx.diag is not None:
        ctx.diag["extraction"] = pages_diagnostic(ctx.content.pages)
