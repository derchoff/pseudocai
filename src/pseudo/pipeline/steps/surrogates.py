"""Valeurs fictives (mode PSEUDO_OUTPUT_MODE=substitution) et emplacements des remplacements.

Les jetons ont servi à la vérification et à la classification ; seul le document publié reçoit des
valeurs réalistes, posées ici juste avant le rendu.
"""

from __future__ import annotations

import re
from typing import Callable

from ...fields import BUILTIN_STRATEGIES, BUILTIN_SUBSTITUTIONS, DEFAULT_STRATEGY, DEFAULT_SUBSTITUTION, FieldCatalog
from ...models import DocumentContent, Line, Span
from ...pseudonymizer import normalize
from ...spans import pseudonymise_document, render_text
from ...surrogates import SurrogateFactory
from ..context import PipelineContext, step


@step("valeurs_fictives")
def substitute(ctx: PipelineContext) -> None:
    """Pose les valeurs fictives (mode substitution) puis relève les occurrences (ctx.occurrences)."""
    settings, content = ctx.settings, ctx.content
    ctx.report["mode_sortie"] = settings.output_mode
    if settings.output_mode == "substitution":
        if ctx.services.surrogates is None:
            raise RuntimeError("Mode substitution sans fabrique de valeurs fictives (services.surrogates)")
        assign_surrogates(ctx.spans, content, ctx.catalog, ctx.services.surrogates)
        ctx.text = render_text(content, pseudonymise_document(content, ctx.spans))
    ctx.occurrences = occurrences(ctx.spans, content)


_CIVILITY_RE = re.compile(r"\b(?:M\.|Mr\b|Monsieur\b|Mme\b|Mlle\b|Madame\b|Mademoiselle\b)", re.IGNORECASE)


def _gender_hint(line: Line, span: Span) -> str | None:
    """Civilité la plus proche avant la valeur (« M. DUPONT Jean » -> masculin)."""
    found = _CIVILITY_RE.findall(line.text[: span.start])
    if not found:
        return None
    return "f" if found[-1].lower().startswith(("mme", "mlle", "madame", "mademoiselle")) else "m"


def assign_surrogates(
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


def occurrences(spans: list[Span], content: DocumentContent) -> list[dict]:
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
