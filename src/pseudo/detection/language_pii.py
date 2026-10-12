"""Filet de sécurité statistique : Azure AI Language (détection PII)."""

from __future__ import annotations

import logging

from ..models import Line, Span

log = logging.getLogger(__name__)

MAX_DOC_CHARS = 5000  # limite du service : 5 120 caractères par document
MAX_DOCS_PER_CALL = 5


def parse_category_map(spec: str) -> dict[str, str]:
    """'Person:PERSONNE,Email:EMAIL' -> {'Person': 'PERSONNE', 'Email': 'EMAIL'}"""
    mapping = {}
    for item in spec.split(","):
        if ":" in item:
            src, dst = item.split(":", 1)
            mapping[src.strip()] = dst.strip()
    return mapping


class LanguagePiiDetector:
    """Filet de sécurité : détection de données personnelles par Azure AI Language (PII)."""

    def __init__(self, client, category_map: dict[str, str], min_confidence: float, language: str = "fr"):
        """category_map : catégorie Azure -> type interne ; les détections sous min_confidence sont ignorées."""
        self._client = client
        self._map = category_map
        self._min_conf = min_confidence
        self._language = language

    def detect(self, lines: list[Line]) -> list[Span]:
        """Envoie les lignes par blocs à Azure AI Language et renvoie les zones détectées, ligne par ligne."""
        chunks = self._chunk(lines)
        spans: list[Span] = []
        for i in range(0, len(chunks), MAX_DOCS_PER_CALL):
            batch = chunks[i:i + MAX_DOCS_PER_CALL]
            results = self._client.recognize_pii_entities([text for text, _ in batch], language=self._language)
            for (text, layout), result in zip(batch, results):
                if result.is_error:
                    raise RuntimeError(f"Azure AI Language : {result.error.code}")
                for ent in result.entities:
                    etype = self._map.get(ent.category)
                    if etype is None or ent.confidence_score < self._min_conf:
                        continue
                    spans += _to_line_spans(layout, ent.offset, ent.offset + ent.length, etype)
        log.info("Azure AI Language : %d détection(s)", len(spans))
        return spans

    @staticmethod
    def _chunk(lines: list[Line]) -> list[tuple[str, list[tuple[int, Line]]]]:
        """Regroupe les lignes en documents ≤ MAX_DOC_CHARS ; garde la position de chaque ligne."""
        chunks = []
        parts: list[str] = []
        layout: list[tuple[int, Line]] = []
        size = 0
        for line in lines:
            text = line.text[:MAX_DOC_CHARS]
            if parts and size + len(text) + 1 > MAX_DOC_CHARS:
                chunks.append(("\n".join(parts), layout))
                parts, layout, size = [], [], 0
            layout.append((size, line))
            parts.append(text)
            size += len(text) + 1
        if parts:
            chunks.append(("\n".join(parts), layout))
        return chunks


def _to_line_spans(layout: list[tuple[int, Line]], start: int, end: int, etype: str) -> list[Span]:
    """Découpe une détection [start, end[ d'un bloc de texte en zones sur chacune des lignes qu'elle couvre."""
    spans = []
    for offset, line in layout:
        a = max(start, offset)
        b = min(end, offset + len(line.text))
        if a < b:
            spans.append(Span(line.id, a - offset, b - offset, etype, "language"))
    return spans
