"""Localisation des entités, propagation, fusion des détections et texte pseudonymisé."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from .models import SOURCE_PRIORITY, DocumentContent, Entity, Line, Span

_EDGE_CHARS = " \t\r\n,;:"


# --------------------------------------------------------------------------- recherche


def _flex_pattern(needle: str, word_boundary: bool = False) -> re.Pattern | None:
    """Motif tolérant aux espaces (ajoutés ou supprimés par l'OCR) et insensible à la casse."""
    chars = [c for c in needle if not c.isspace()]
    if not chars:
        return None
    body = r"\s*".join(re.escape(c) for c in chars)
    if word_boundary:
        body = rf"(?<!\w){body}(?!\w)"
    return re.compile(body, re.IGNORECASE)


def find_strict(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Positions de needle dans haystack, sans tenir compte de la casse.

    À défaut, tolère des espaces ajoutés ou manquants (fréquents à l'OCR).
    """
    occurrences = [(m.start(), m.end()) for m in re.finditer(re.escape(needle), haystack, re.IGNORECASE)]
    if occurrences:
        return occurrences
    pattern = _flex_pattern(needle)
    return [(m.start(), m.end()) for m in pattern.finditer(haystack)] if pattern else []


def find_fuzzy(haystack: str, needle: str, min_score: float) -> list[tuple[int, int]]:
    """Meilleure position approchée de needle (score >= min_score), étendue aux mots entiers."""
    if len(needle) < 4 or not haystack:
        return []
    lowered = haystack.lower()
    if len(lowered) != len(haystack):
        return []
    alignment = fuzz.partial_ratio_alignment(needle.lower(), lowered)
    if alignment is None or alignment.score < min_score:
        return []
    a, b = alignment.dest_start, alignment.dest_end
    # Étend aux limites de mots pour ne jamais laisser un morceau de mot en clair.
    while a > 0 and haystack[a - 1].isalnum():
        a -= 1
    while b < len(haystack) and haystack[b].isalnum():
        b += 1
    a, b = trim(haystack, a, b)
    return [(a, b)] if a < b else []


def trim(text: str, a: int, b: int) -> tuple[int, int]:
    """Retire espaces et ponctuation en début et fin de [a, b[."""
    while a < b and text[a] in _EDGE_CHARS:
        a += 1
    while b > a and text[b - 1] in _EDGE_CHARS:
        b -= 1
    return a, b


# --------------------------------------------------------------------------- localisation


def resolve_entities(
    entities: list[Entity],
    doc: DocumentContent,
    fuzzy_min_score: float,
    diagnostics: list[dict] | None = None,
) -> tuple[list[Span], list[Entity]]:
    """Transforme les entités texte des agents en zones localisées. Retourne (spans, non_localisées).

    diagnostics : si fourni, reçoit une entrée par entité (type, ligne, méthode), sans la valeur.
    """
    group_values = _group_values(entities, doc)
    spans: list[Span] = []
    unresolved: list[Entity] = []
    for e in entities:
        value = group_values.get(e.group) if e.group else None
        found: list[tuple[Line, int, int]] = []
        method = "echec"
        line = doc.line(e.line_id) if e.line_id else None
        if line is not None:
            occ, method = _find_in_line(line.text, e.text, fuzzy_min_score)
            found = [(line, a, b) for a, b in occ]
        if not found:
            # Mauvais identifiant de ligne renvoyé par l'agent : recherche dans tout le document.
            for other in doc.lines():
                found += [(other, a, b) for a, b in find_strict(other.text, e.text)]
            method = "autre_ligne" if found else "echec"
        if diagnostics is not None:
            entry = {"type": e.type, "source": e.source, "ligne": e.line_id or None, "methode": method}
            if not found:
                entry.update(explain_unresolved(e, doc))
            elif method == "approchee":
                # Une valeur coupée sur deux lignes peut n'être retrouvée qu'en partie dans la ligne
                # indiquée : la longueur masquée, plus courte, le signale.
                entry["longueur"] = len(e.text)
                entry["longueur_masquee"] = sum(b - a for _, a, b in found)
            diagnostics.append(entry)
        if not found:
            unresolved.append(e)
            continue
        for ln, a, b in found:
            a, b = trim(ln.text, a, b)
            if a < b:
                spans.append(Span(ln.id, a, b, e.type, e.source, value=value))
    return spans, unresolved


def _find_in_line(text: str, needle: str, fuzzy_min_score: float) -> tuple[list[tuple[int, int]], str]:
    """Recherche dans la ligne indiquée, avec la méthode qui a réussi (exacte, espaces, approchee)."""
    occ = [(m.start(), m.end()) for m in re.finditer(re.escape(needle), text, re.IGNORECASE)]
    if occ:
        return occ, "exacte"
    pattern = _flex_pattern(needle)
    occ = [(m.start(), m.end()) for m in pattern.finditer(text)] if pattern else []
    if occ:
        return occ, "espaces"
    occ = find_fuzzy(text, needle, fuzzy_min_score)
    return occ, ("approchee" if occ else "echec")


# --------------------------------------------------------------------------- diagnostic

_FOLD_TABLE = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'", "–": "-", "—": "-", "‑": "-",
                             "‐": "-", "­": "", "​": "", " ": " ", " ": " "})


def fold(text: str) -> str:
    """Forme de comparaison : sans accents, ligatures, ponctuation ni espaces, en minuscules."""
    text = unicodedata.normalize("NFKD", text.translate(_FOLD_TABLE))
    return "".join(c for c in text if c.isalnum()).casefold()


def explain_unresolved(e: Entity, doc: DocumentContent) -> dict:
    """Pourquoi une entité n'a pas été localisée, sans jamais renvoyer sa valeur.

    longueur et mots de la valeur ; meilleur score approché dans la ligne indiquée et dans tout le
    document ; et si elle aurait été trouvée en joignant deux lignes consécutives, ou en ignorant
    accents et ponctuation. Ces tests ne masquent rien : ils servent à choisir les correctifs.
    """
    lines = doc.lines()
    needle = e.text
    folded = fold(needle)
    index = {ln.id: i for i, ln in enumerate(lines)}
    i = index.get(e.line_id)

    def score(text: str) -> float:
        """Score partiel de la valeur dans un texte (0 à 100)."""
        return fuzz.partial_ratio(needle.lower(), text.lower()) if text else 0.0

    def joined(a: Line, b: Line) -> list[str]:
        """Deux lignes consécutives mises bout à bout (avec ou sans mot coupé par un tiret)."""
        first = a.text.rstrip()
        out = [f"{first} {b.text.lstrip()}"]
        if first.endswith(("-", "­")):
            out.append(first[:-1] + b.text.lstrip())
        return out

    pairs = list(zip(lines, lines[1:]))
    if i is not None:
        pairs = [(a, b) for a, b in pairs if index[a.id] in (i - 1, i)]
    two_lines = any(
        find_strict(t, needle) or (folded and folded in fold(t)) for a, b in pairs for t in joined(a, b)
    )
    return {
        "ligne_connue": i is not None,
        "longueur": len(needle),
        "mots": len(needle.split()),
        "meilleur_score_ligne": round(score(lines[i].text)) if i is not None else None,
        "meilleur_score_document": round(max((score(ln.text) for ln in lines), default=0.0)),
        "trouvee_sur_2_lignes": bool(two_lines),
        "trouvee_sans_accents_ponctuation": bool(folded) and any(folded in fold(ln.text) for ln in lines),
    }


def _group_values(entities: list[Entity], doc: DocumentContent) -> dict[str, str]:
    """Valeur complète de chaque groupe d'entités (valeur répartie sur plusieurs lignes)."""
    groups: dict[str, list[Entity]] = defaultdict(list)
    for e in entities:
        if e.group:
            groups[e.group].append(e)
    return {
        g: " ".join(e.text for e in sorted(items, key=lambda e: doc.order(e.line_id)))
        for g, items in groups.items()
    }


def propagate(spans: list[Span], doc: DocumentContent, min_length: int) -> list[Span]:
    """Recherche toutes les autres occurrences des valeurs déjà détectées (mot entier)."""
    seen: set[tuple[str, str]] = set()
    new_spans: list[Span] = []
    for s in spans:
        line = doc.line(s.line_id)
        if line is None:
            continue
        text = s.text(line)
        key = (s.type, text.casefold())
        if key in seen or len(text.strip()) < min_length:
            continue
        seen.add(key)
        pattern = _flex_pattern(text, word_boundary=True)
        if pattern is None:
            continue
        for other in doc.lines():
            for m in pattern.finditer(other.text):
                new_spans.append(Span(other.id, m.start(), m.end(), s.type, "propagation", value=s.value))
    return new_spans


# --------------------------------------------------------------------------- fusion


def merge_spans(spans: list[Span], doc: DocumentContent) -> list[Span]:
    """Supprime les chevauchements : la source la plus fiable gagne, les autres sont rognées.

    Exception : une adresse e-mail passe en premier, d'un seul tenant. Sinon un nom retrouvé à
    l'intérieur (« durand » dans marie.durand@…) la découperait en morceaux remplacés séparément.
    """
    by_line: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        if doc.line(s.line_id) is not None:
            by_line[s.line_id].append(s)
    merged: list[Span] = []
    for line_id, items in by_line.items():
        text = doc.line(line_id).text
        covered = [False] * len(text)
        items.sort(key=lambda s: (s.type != "EMAIL", SOURCE_PRIORITY.get(s.source, 9), -(s.end - s.start), s.start))
        for s in items:
            a, b = max(0, s.start), min(len(text), s.end)
            full = trim(text, a, b)
            for ra, rb in _uncovered_runs(covered, a, b):
                ra, rb = trim(text, ra, rb)
                if ra >= rb or not any(c.isalnum() for c in text[ra:rb]):
                    continue
                value = s.value if (ra, rb) == full else None
                merged.append(Span(line_id, ra, rb, s.type, s.source, value=value))
                for i in range(ra, rb):
                    covered[i] = True
    merged.sort(key=lambda s: (doc.order(s.line_id), s.start))
    return merged


def _uncovered_runs(covered: list[bool], a: int, b: int) -> list[tuple[int, int]]:
    """Intervalles de [a, b[ qui ne sont pas encore couverts."""
    runs, start = [], None
    for i in range(a, b):
        if not covered[i] and start is None:
            start = i
        elif covered[i] and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, b))
    return runs


def coverage_signature(spans: list[Span]) -> set[tuple[str, int]]:
    """Ensemble des caractères (ligne, position) couverts par les zones."""
    return {(s.line_id, i) for s in spans for i in range(s.start, s.end)}


# --------------------------------------------------------------------------- texte pseudonymisé


@dataclass
class PseudoLine:
    """Ligne pseudonymisée, avec la correspondance des positions vers la ligne d'origine."""
    id: str
    text: str
    # (début_pseudo, fin_pseudo, début_original, fin_original, est_un_jeton)
    segments: list[tuple[int, int, int, int, bool]]

    def to_original(self, a: int, b: int) -> tuple[int, int]:
        """Convertit [a, b[ de la ligne pseudonymisée en positions dans la ligne d'origine."""
        return self._map(a, is_end=False), self._map(b, is_end=True)

    def _map(self, pos: int, is_end: bool) -> int:
        """Position d'origine d'une position du texte pseudonymisé (un jeton compte en entier)."""
        for ps, pe, os_, oe, is_token in self.segments:
            if ps <= pos < pe or (is_end and pos == pe):
                if is_token:
                    return oe if is_end else os_
                return os_ + (pos - ps)
        return self.segments[-1][3] if self.segments else 0


def pseudonymise_line(line: Line, spans: list[Span]) -> PseudoLine:
    """Remplace les zones de la ligne par leur remplacement (valeur fictive ou jeton)."""
    parts: list[str] = []
    segments = []
    pos_orig = 0
    pos_pseudo = 0

    def add(text: str, os_: int, oe: int, is_token: bool) -> None:
        """Ajoute un morceau au texte et note sa correspondance avec l'original."""
        nonlocal pos_pseudo
        if not text and not is_token:
            return
        segments.append((pos_pseudo, pos_pseudo + len(text), os_, oe, is_token))
        parts.append(text)
        pos_pseudo += len(text)

    for s in sorted(spans, key=lambda s: s.start):
        add(line.text[pos_orig:s.start], pos_orig, s.start, False)
        add(s.replacement or f"[{s.type}]", s.start, s.end, True)
        pos_orig = s.end
    add(line.text[pos_orig:], pos_orig, len(line.text), False)
    return PseudoLine(id=line.id, text="".join(parts), segments=segments)


def pseudonymise_document(doc: DocumentContent, spans: list[Span]) -> list[PseudoLine]:
    """Pseudonymise toutes les lignes du document."""
    by_line: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        by_line[s.line_id].append(s)
    return [pseudonymise_line(line, by_line.get(line.id, [])) for line in doc.lines()]


def render_text(doc: DocumentContent, pseudo_lines: list[PseudoLine]) -> str:
    """Texte pseudonymisé complet, page par page (fichier .pseudonymise.txt)."""
    by_id = {pl.id: pl for pl in pseudo_lines}
    out = []
    for page in doc.pages:
        out.append(f"===== Page {page.index + 1} =====")
        out += [by_id[line.id].text for line in page.lines if line.id in by_id]
    return "\n".join(out) + "\n"
