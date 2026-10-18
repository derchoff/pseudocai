"""Orchestration de bout en bout pour un PDF, une image préparée ou un texte déjà extrait (corps et
en-têtes d'un mail). Indépendante d'Azure Functions, donc testable."""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Callable

import pymupdf

from ..catalog import CatalogError
from ..classification import (
    STATUT_DESACTIVEE,
    STATUT_ERREUR,
    STATUT_NON_APPLICABLE,
    Classification,
    decide,
    unclassified,
)
from ..config import Settings
from ..detection.regex_rules import detect_regex
from ..exceptions import apply_exceptions
from ..extraction import extract_document
from ..fields import BUILTIN_STRATEGIES, BUILTIN_SUBSTITUTIONS, DEFAULT_STRATEGY, DEFAULT_SUBSTITUTION, FieldCatalog
from ..models import DocumentContent, Entity, Line, PageContent, Span
from ..pseudonymizer import MappingRecord, Pseudonymizer, normalize
from ..redaction import normalize_rotation, render_pdf
from ..surrogates import SurrogateFactory
from ..spans import (
    coverage_signature,
    merge_spans,
    propagate,
    pseudonymise_document,
    render_text,
    resolve_entities,
)
from ..statistics.usage import add_agent, agent_usage, empty_consumption
from .diagnostic import agent_stats, localisation_diagnostic, pages_diagnostic, round_diagnostic
from .models import PipelineInput, PipelineResult, Services

log = logging.getLogger(__name__)


def run_pipeline(source: PipelineInput | bytes, settings: Settings, services: Services) -> PipelineResult:
    """Pseudonymise un document de bout en bout.

    Contrôles, extraction (texte et OCR), détection, jetons, vérification, classification, rendu.
    Ne touche à aucun stockage : la publication est faite par l'appelant.
    """
    if isinstance(source, bytes):
        source = PipelineInput(pdf=source)
    started = time.monotonic()
    report: dict = {"pipeline_version": "0.2.0"}
    if source.source:
        report["source"] = source.source
    # Consommation des services facturés (OCR, agents IA, Language), même en cas de quarantaine.
    consumption = report["consommation"] = empty_consumption()
    reasons: list[str] = []
    # Rapport détaillé (REPORT_DETAIL=diagnostic) : ce que chaque étape a fait, jamais les valeurs.
    diag: dict | None = None
    if settings.report_detail == "diagnostic":
        diag = report["diagnostic"] = {"durees_s": {}}

    if source.content is not None:
        content = source.content
        lines = content.lines()
        report["lignes"] = len(lines)
        if diag is not None:
            diag["extraction"] = pages_diagnostic(content.pages)
    else:
        extracted = _open_and_extract(source, settings, services, report, reasons)
        if isinstance(extracted, PipelineResult):
            return extracted
        content = extracted
        lines = content.lines()

    # 3. Détection (agent IA + filets de sécurité) -----------------------------------------
    catalog = services.fields() if services.fields else None
    if catalog is not None:
        report["champs"] = list(catalog.types)
    step = time.monotonic()
    entities = services.detector.detect(lines)
    consumption["ia"]["detection"] = agent_usage(services.detector)
    if source.content is None:
        # Pages d'un vrai document (PDF, image) dont le texte part aux agents IA : celles qui ont au
        # moins une ligne non vide. Un texte déjà extrait (corps de mail, JSON) n'a pas de page.
        consumption["pages_ia"] = len({line.page for line in lines if line.text.strip()})
    llm_seconds = time.monotonic() - step
    located: list[dict] | None = [] if diag is not None else None
    spans, unresolved = resolve_entities(entities, content, settings.fuzzy_min_score, located)
    regex_spans = detect_regex(lines)
    spans += regex_spans
    language_spans: list[Span] | None = None
    language_seconds = 0.0
    if services.language is not None:
        language_started = time.monotonic()
        language_spans = services.language.detect(lines)
        language_stats = getattr(services.language, "last_stats", None) or {}
        for key in ("enregistrements", "caracteres"):
            consumption["language"][key] += int(language_stats.get(key, 0) or 0)
        language_seconds = time.monotonic() - language_started
        spans += language_spans
    before_merge = len(spans)
    spans = merge_spans(spans, content)
    propagated = propagate(spans, content, settings.propagation_min_length)
    spans = merge_spans(spans + propagated, content)
    exceptions = catalog.exceptions if catalog is not None else None
    before_exceptions = len(spans)
    spans = apply_exceptions(spans, content, exceptions)
    _assign_tokens(spans, content, services.pseudonymizer, catalog)
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

    # 4. Contrôle qualité par l'agent vérificateur -----------------------------------------
    rounds_done = 0
    still_finding = False
    step = time.monotonic()
    if services.verifier is not None:
        rounds: list[dict] = []
        for _ in range(settings.verification_rounds):
            rounds_done += 1
            round_started = time.monotonic()
            pseudo_lines = pseudonymise_document(content, spans)
            residual_entities = services.verifier.verify([(pl.id, pl.text) for pl in pseudo_lines])
            add_agent(consumption["ia"]["verification"], agent_usage(services.verifier))
            verifier_seconds = time.monotonic() - round_started
            residual_located: list[dict] | None = [] if diag is not None else None
            residual_spans, residual_unresolved = _residuals_to_original(
                residual_entities, pseudo_lines, content, settings.fuzzy_min_score, residual_located
            )
            unresolved += residual_unresolved
            before = coverage_signature(spans)
            candidate = merge_spans(spans + residual_spans, content)
            candidate = merge_spans(candidate + propagate(candidate, content, settings.propagation_min_length), content)
            candidate = apply_exceptions(candidate, content, exceptions)
            after = coverage_signature(candidate)
            still_finding = after != before
            if diag is not None:
                rounds.append(
                    round_diagnostic(
                        rounds_done, services.verifier, residual_entities, residual_located,
                        before, after, candidate, verifier_seconds,
                    )
                )
            if not still_finding:
                break
            spans = candidate
            _assign_tokens(spans, content, services.pseudonymizer, catalog)
        report["prompt_verification_sha256"] = services.verifier.prompt_sha256
        if still_finding:
            reasons.append("residus_apres_verification")
        if diag is not None:
            diag["verification"] = rounds
            diag["durees_s"]["verification"] = round(time.monotonic() - step, 2)
    report["tours_verification"] = rounds_done

    if unresolved:
        report["entites_non_localisees"] = len(unresolved)
        if settings.quarantine_on_unlocated:
            reasons.append("entites_non_localisees")

    report["entites_par_type"] = dict(Counter(s.type for s in spans))
    report["entites_par_source"] = dict(Counter(s.source for s in spans))
    records = _mapping_records(spans, content, services, catalog)
    report["jetons_distincts"] = len(records)
    text = render_text(content, pseudonymise_document(content, spans))

    token_text = text

    # 5. Classification du type de document (sur le texte pseudonymisé) ----------------------
    step = time.monotonic()
    if source.classify:
        classification = classify_text(text, settings, services, consumption)
    else:
        classification = unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder)
    report["classification"] = classification.to_dict()
    if diag is not None and source.classify:
        diag["durees_s"]["classification"] = round(time.monotonic() - step, 2)

    # 6. Valeurs fictives (mode substitution) : les jetons ont servi à la vérification et à la
    #    classification ; seul le document publié reçoit des valeurs réalistes.
    report["mode_sortie"] = settings.output_mode
    if settings.output_mode == "substitution":
        if services.surrogates is None:
            raise RuntimeError("Mode substitution sans fabrique de valeurs fictives (services.surrogates)")
        _assign_surrogates(spans, content, catalog, services.surrogates)
        text = render_text(content, pseudonymise_document(content, spans))
    occurrences = _occurrences(spans, content)

    # 7. Rendu (un texte déjà extrait est rendu par l'appelant) -------------------------------
    output = None
    if source.content is None or source.renderer is not None:
        step = time.monotonic()
        renderer = source.renderer or render_pdf
        output = renderer(source.pdf, content, spans, settings.ocr_render_dpi)
        if diag is not None:
            diag["durees_s"]["rendu"] = round(time.monotonic() - step, 2)
    report["duree_secondes"] = round(time.monotonic() - started, 2)

    status = "quarantine" if reasons else "ok"
    report["statut"] = status
    report["motifs"] = reasons
    return PipelineResult(
        status=status,
        reasons=reasons,
        output=output,
        text=text,
        records=records,
        report=report,
        classification=classification,
        occurrences=occurrences,
        token_text=token_text,
        content=content,
        spans=spans,
    )


def _open_and_extract(
    source: PipelineInput, settings: Settings, services: Services, report: dict, reasons: list[str]
) -> DocumentContent | PipelineResult:
    """Ouvre le PDF, le contrôle et en extrait le texte positionné (couche texte et OCR).

    Renvoie un PipelineResult de quarantaine si le document est refusé avant la détection.
    """
    pdf_bytes = source.pdf
    started = time.monotonic()
    # 1. Ouverture et contrôles préalables -------------------------------------------------
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 - tout PDF illisible part en quarantaine
        return _rejected("pdf_illisible", report, settings)
    if not doc.is_pdf:
        # MuPDF reconnaît d'autres formats même en mode « pdf » (une image renommée en .pdf
        # s'ouvrirait) : un contenu non conforme à l'extension n'est jamais réinterprété.
        doc.close()
        return _rejected("pdf_illisible", report, settings)
    if doc.needs_pass:
        return _rejected("pdf_chiffre", report, settings)
    if doc.page_count == 0:
        return _rejected("pdf_vide", report, settings)
    if doc.page_count > settings.max_pages:
        return _rejected("trop_de_pages", {**report, "pages": doc.page_count}, settings)
    if hasattr(doc, "bake"):
        # Valeurs de formulaires et annotations deviennent du contenu de page : elles sont
        # ainsi extraites, détectées et caviardées comme le reste.
        doc.bake(annots=True, widgets=True)
    normalize_rotation(doc)

    # 2. Extraction du texte positionné ----------------------------------------------------
    pages = extract_document(
        pdf_bytes,
        doc,
        source.ocr or services.ocr,
        min_native_chars=settings.min_native_chars,
        image_coverage_threshold=settings.image_coverage_ocr_threshold,
        force_ocr=settings.force_ocr or source.force_ocr,
        ocr_native_pages=settings.ocr_native_pages,
        usage=report.get("consommation"),
    )
    doc.close()
    content = DocumentContent(pages=pages)
    lines = content.lines()
    if "diagnostic" in report:
        report["diagnostic"]["extraction"] = pages_diagnostic(pages)
        report["diagnostic"]["durees_s"]["extraction"] = round(time.monotonic() - started, 2)
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
            return _rejected("manuscrit_detecte", report, settings)
    return content


_CIVILITY_RE = re.compile(r"\b(?:M\.|Mr\b|Monsieur\b|Mme\b|Mlle\b|Madame\b|Mademoiselle\b)", re.IGNORECASE)


def _gender_hint(line: Line, span: Span) -> str | None:
    """Civilité la plus proche avant la valeur (« M. DUPONT Jean » -> masculin)."""
    found = _CIVILITY_RE.findall(line.text[: span.start])
    if not found:
        return None
    return "f" if found[-1].lower().startswith(("mme", "mlle", "madame", "mademoiselle")) else "m"


def _assign_surrogates(
    spans: list[Span], content: DocumentContent, catalog: FieldCatalog | None, factory: SurrogateFactory
) -> None:
    """Pose sur chaque zone sa valeur fictive : déterministe, au format de l'original, et différente
    de l'originale comme de toute autre vraie valeur du document (comparaison après normalisation)."""

    def strategy(entity_type: str) -> str:
        """Normalisation du type (référentiel, sinon valeur intégrée au code)."""
        return (catalog.strategy(entity_type) if catalog else None) or BUILTIN_STRATEGIES.get(entity_type, DEFAULT_STRATEGY)

    def generator(entity_type: str) -> str:
        """Générateur de valeur fictive du type (référentiel, sinon valeur intégrée au code)."""
        if catalog is not None:
            return catalog.substitution(entity_type)
        return BUILTIN_SUBSTITUTIONS.get(entity_type, DEFAULT_SUBSTITUTION)

    texts = [(s, s.text(content.line(s.line_id))) for s in spans]
    originals: dict[str, set[str]] = {}
    for s, text in texts:
        strat = strategy(s.type)
        originals.setdefault(strat, set()).add(normalize(strat, text))
    # Une même valeur écrite dans des casses différentes (« Dupont », « DUPONT ») reçoit la même
    # valeur fictive, dans la casse de chaque occurrence : sinon l'indice de genre, qui dépend de la
    # civilité voisine, pourrait donner deux prénoms différents.
    chosen: dict[tuple[str, str], str] = {}
    pieces = _split_group_surrogates(texts, content, lambda value, s: factory.unique(
        generator(s.type),
        value,
        key=f"{s.type}|{normalize(strategy(s.type), value)}",
        forbidden=originals[strategy(s.type)],
        normalize=lambda v, strat=strategy(s.type): normalize(strat, v),
        gender_hint=_gender_hint(content.line(s.line_id), s),
    ))
    for s, text in texts:
        if id(s) in pieces:
            s.surrogate = pieces[id(s)]
            continue
        strat = strategy(s.type)
        same = (s.type, text.strip().casefold())
        if same in chosen:
            s.surrogate = chosen[same] if strat == "email" else _same_case(text, chosen[same])
            continue
        s.surrogate = factory.unique(
            generator(s.type),
            text,
            key=f"{s.type}|{normalize(strat, text)}",
            forbidden=originals[strat],
            normalize=lambda v, strat=strat: normalize(strat, v),
            gender_hint=_gender_hint(content.line(s.line_id), s),
        )
        if strat == "email" and catalog is not None:
            s.surrogate = catalog.exceptions.with_listed_domain(text, s.surrogate)
        chosen[same] = s.surrogate


def _split_group_surrogates(
    texts: list[tuple[Span, str]], content: DocumentContent, make: Callable[[str, Span], str]
) -> dict[int, str]:
    """Valeur fictive des valeurs coupées sur plusieurs lignes (même group : date en lettres, nom
    coupé par un tiret…) : une seule valeur fictive, fabriquée pour la valeur complète, puis répartie
    sur les fragments au prorata de leurs mots. Sinon chaque fragment recevrait sa propre valeur
    (« vingt-trois mars mil neuf » / « cent cinquante-deux » : une date complète puis du charabia).

    Renvoie id(zone) -> morceau de la valeur fictive, pour les fragments d'un groupe complet.
    """
    runs: dict[tuple[str, str], list[tuple[Span, str]]] = {}
    for s, text in sorted(texts, key=lambda t: (content.order(t[0].line_id), t[0].start)):
        if s.value is not None and s.value.split() != text.split():
            runs.setdefault((s.type, s.value), []).append((s, text))
    out: dict[int, str] = {}
    for (_, value), items in runs.items():
        target = value.split()
        start = 0
        while start < len(items):
            # Plus courte suite de fragments consécutifs qui reconstitue la valeur complète.
            words: list[str] = []
            for end in range(start, len(items)):
                words += items[end][1].split()
                if words == target or len(words) >= len(target):
                    break
            if words != target:
                start += 1
                continue
            run = items[start:end + 1]
            surrogate = make(value, run[0][0]).split()
            counts = [len(t.split()) for _, t in run]
            position = 0
            for i, ((s, _), n) in enumerate(zip(run, counts)):
                take = surrogate[position:] if i == len(run) - 1 else surrogate[position:position + n]
                out[id(s)] = " ".join(take)
                position += n
            start = end + 1
    return out


def _same_case(template: str, surrogate: str) -> str:
    """La valeur fictive dans la casse de l'occurrence : MAJUSCULES, minuscules, ou Initiale."""
    letters = [c for c in template if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return surrogate.upper()
    if letters and all(c.islower() for c in letters):
        return surrogate.lower()
    if surrogate.isupper() or surrogate.islower():
        return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), surrogate)
    return surrogate


def _occurrences(spans: list[Span], content: DocumentContent) -> list[dict]:
    """Emplacements remplacés, en points de la page (repère PyMuPDF), sans valeur d'origine."""
    out = []
    for s in spans:
        line = content.line(s.line_id)
        words = [w for w in line.words if w.start < s.end and w.end > s.start]
        if not words:
            continue
        x0 = min(w.rect[0] for w in words)
        y0 = min(w.rect[1] for w in words)
        x1 = max(w.rect[2] for w in words)
        y1 = max(w.rect[3] for w in words)
        out.append(
            {
                "jeton": (s.token or "").strip("[]"),
                "type": s.type,
                "valeur_fictive": s.surrogate,
                "page": line.page + 1,
                "ligne": s.line_id,
                "position": [round(v, 2) for v in (x0, y0, x1, y1)],
                "caracteres": [s.start, s.end],
                "quadrilateres": [[round(v, 2) for v in w.quad] for w in words if w.quad] or None,
            }
        )
    return out


def _rejected(reason: str, report: dict, settings: Settings) -> PipelineResult:
    """Document rejeté avant la détection : quarantaine, sans classification."""
    classification = unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder)
    report = {**report, "statut": "quarantine", "motifs": [reason], "classification": classification.to_dict()}
    return PipelineResult(status="quarantine", reasons=[reason], report=report, classification=classification)


def classify_text(
    text: str, settings: Settings, services: Services, consumption: dict | None = None
) -> Classification:
    """Classe le texte pseudonymisé ; une erreur de l'agent range le document en « non classé ».

    Un référentiel invalide lève l'erreur, pour que le message soit réessayé. consumption : si donné,
    reçoit les appels et jetons de l'agent de classification (clé ia.classification).
    """
    if not settings.classification_enabled or services.classifier is None:
        return unclassified(STATUT_DESACTIVEE, settings.unclassified_folder)
    try:
        raw, doctypes, prompt_sha256 = services.classifier.classify(text)
    except CatalogError:
        raise  # référentiel invalide : erreur de configuration, le message sera réessayé
    except Exception:  # noqa: BLE001 - une classification ratée ne doit pas bloquer la pseudonymisation
        log.exception("Classification en échec : document rangé dans « %s »", settings.unclassified_folder)
        return unclassified(STATUT_ERREUR, settings.unclassified_folder, services.classifier.prompt_sha256)
    finally:
        if consumption is not None:
            consumption.setdefault("ia", {})["classification"] = agent_usage(services.classifier)
    return decide(
        raw,
        doctypes,
        min_confidence=settings.classification_min_confidence,
        secondary_min_confidence=settings.classification_secondary_min_confidence,
        unclassified_folder=settings.unclassified_folder,
        prompt_sha256=prompt_sha256,
    )


def _strategy(catalog: FieldCatalog | None, entity_type: str) -> str | None:
    """Normalisation du type selon le référentiel, ou None sans référentiel."""
    return catalog.strategy(entity_type) if catalog is not None else None


def _assign_tokens(
    spans: list[Span], content: DocumentContent, pseudonymizer: Pseudonymizer, catalog: FieldCatalog | None
) -> None:
    """Calcule le jeton de chaque zone à partir de sa valeur normalisée."""
    for s in spans:
        line = content.line(s.line_id)
        s.token = pseudonymizer.token(s.type, s.pseudonym_value(line), _strategy(catalog, s.type))


def _residuals_to_original(
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


def _mapping_records(
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
            fingerprint=services.pseudonymizer.fingerprint(s.type, value, _strategy(catalog, s.type)),
            ciphertext=services.cipher.encrypt(s.token, value),
        )
    return list(records.values())


# --------------------------------------------------------------------------- rapport « diagnostic »
# Rien ici ne contient de valeur d'origine : types, identifiants de ligne, nombres, durées.
