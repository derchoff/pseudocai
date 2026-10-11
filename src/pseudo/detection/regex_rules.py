"""Filet de sécurité déterministe : formats très structurés (NIR, e-mail, téléphone, date de naissance)."""

from __future__ import annotations

import re

from ..models import Line, Span

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

PHONE_FR_RE = re.compile(
    r"(?<![\d+])(?:(?:\+|00)\s?33\s?(?:\(0\)\s?)?[1-9]|0[1-9])(?:[\s.\-]?\d{2}){4}(?!\d)"
)

NIR_RE = re.compile(
    r"(?<![0-9A-Za-z])"
    r"([1-478])[\s.]?(\d{2})[\s.]?(\d{2})[\s.]?(\d{2}|2[ABab])[\s.]?(\d{3})[\s.]?(\d{3})"
    r"(?:[\s.]?(\d{2}))?"
    r"(?![0-9])"
)
NIR_CONTEXT_RE = re.compile(
    r"(s[ée]cu|n°\s*ss|\bss\b|\bnir\b|insee|immatriculation|assur[ée]|carte\s+vitale)", re.IGNORECASE
)

BIRTH_CONTEXT_RE = re.compile(
    r"(n[ée]e?\s*(?:\(e\))?\s+le|date\s+de\s+naissance|naissance|\bddn\b|\bd\.n\.|date\s+of\s+birth|\bdob\b)",
    re.IGNORECASE,
)
_MONTHS = (
    r"janv(?:ier)?|f[ée]v(?:rier)?|mars|avr(?:il)?|mai|juin|juil(?:let)?|ao[uû]t|"
    r"sept(?:embre)?|oct(?:obre)?|nov(?:embre)?|d[ée]c(?:embre)?"
)
DATE_RES = (
    re.compile(
        r"(?<!\d)(?:0?[1-9]|[12]\d|3[01])\s?[/.\-]\s?(?:0?[1-9]|1[0-2])\s?[/.\-]\s?(?:(?:19|20)\d{2}|\d{2})(?!\d)"
    ),
    re.compile(rf"(?<!\d)(?:1er|0?[1-9]|[12]\d|3[01])\s+(?:{_MONTHS})\.?\s+(?:19|20)\d{{2}}(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)"),
)


def nir_key(first13: str) -> int:
    """Clé de contrôle du NIR (Corse : 2A -> 19, 2B -> 18)."""
    normalized = first13.upper().replace("2A", "19").replace("2B", "18")
    return 97 - (int(normalized) % 97)


def _nir_spans(line: Line) -> list[Span]:
    spans = []
    has_context = bool(NIR_CONTEXT_RE.search(line.text))
    for m in NIR_RE.finditer(line.text):
        first13 = "".join(m.group(i) for i in range(1, 7))
        key = m.group(7)
        valid = key is not None and int(key) == nir_key(first13)
        if valid or has_context:
            spans.append(Span(line.id, m.start(), m.end(), "NIR", "regex"))
    return spans


def _birth_date_spans(line: Line) -> list[Span]:
    ctx = BIRTH_CONTEXT_RE.search(line.text)
    if not ctx:
        return []
    candidates = sorted(
        (m for rx in DATE_RES for m in rx.finditer(line.text) if m.start() >= ctx.start()),
        key=lambda m: m.start(),
    )
    if not candidates:
        return []
    m = candidates[0]
    return [Span(line.id, m.start(), m.end(), "DATE_NAISSANCE", "regex")]


def detect_regex(lines: list[Line]) -> list[Span]:
    spans: list[Span] = []
    for line in lines:
        text = line.text
        spans += [Span(line.id, m.start(), m.end(), "EMAIL", "regex") for m in EMAIL_RE.finditer(text)]
        spans += [Span(line.id, m.start(), m.end(), "TELEPHONE", "regex") for m in PHONE_FR_RE.finditer(text)]
        spans += _nir_spans(line)
        spans += _birth_date_spans(line)
    return spans
