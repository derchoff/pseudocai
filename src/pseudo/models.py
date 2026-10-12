"""Structures de données partagées par les étapes du pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

# Les types de champs ne sont PAS définis ici : ils sont déclarés dans le référentiel Markdown
# (prompts/champs-a-pseudonymiser.md) et lus par pseudo.fields.

# Ordre de priorité des sources quand deux détections se chevauchent (plus petit = prioritaire).
SOURCE_PRIORITY = {"llm": 0, "verifier": 1, "regex": 2, "propagation": 3, "language": 4}

Rect = tuple[float, float, float, float]


@dataclass
class Word:
    """Mot positionné sur la page (points PDF), avec sa confiance OCR et sa place dans le texte de la ligne."""
    text: str
    rect: Rect  # x0, y0, x1, y1 en points PDF
    confidence: float = 1.0
    # Quadrilatère (x1, y1, ..., x4, y4) en points, quand il est connu (OCR). Sur une photo prise de
    # biais, il épouse le mot alors que le rectangle englobant déborderait sur ses voisins.
    quad: tuple[float, ...] | None = None
    start: int = 0  # position dans Line.text
    end: int = 0
    handwritten: bool = False  # mot manuscrit selon l'OCR (styles Document Intelligence)


@dataclass
class Line:
    """Ligne de texte d'une page : identifiant stable, mots positionnés et texte reconstitué."""
    id: str  # "p<page>-l<ligne>" (page et ligne à partir de 1)
    page: int  # index de page (0-based)
    words: list[Word]
    text: str = ""
    source: str = "native"  # "native" (couche texte du PDF) | "ocr" (texte lu par OCR)

    @classmethod
    def build(cls, line_id: str, page: int, words: list[Word], source: str = "native") -> "Line":
        """Construit une ligne à partir de ses mots, joints par une espace, et note leur position dans le texte."""
        parts: list[str] = []
        pos = 0
        for w in words:
            if parts:
                pos += 1
            w.start, w.end = pos, pos + len(w.text)
            parts.append(w.text)
            pos = w.end
        return cls(id=line_id, page=page, words=words, text=" ".join(parts), source=source)


@dataclass
class PageContent:
    """Contenu extrait d'une page : lignes, mode de rendu et confiance OCR moyenne."""
    index: int
    # Mode de rendu : "native" (couche texte conservée, images incrustées caviardées en place)
    # ou "ocr" (page scannée, entièrement re-rastérisée).
    source: str
    lines: list[Line]
    ocr_confidence: float | None = None


@dataclass
class DocumentContent:
    """Contenu extrait du document, avec un accès rapide aux lignes par identifiant."""
    pages: list[PageContent]
    _by_id: dict[str, Line] = field(default_factory=dict, init=False, repr=False)
    _order: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Construit l'index des lignes."""
        self.reindex()

    def reindex(self) -> None:
        """Trie les pages et reconstruit l'index des lignes (à appeler après ajout de lignes)."""
        self.pages.sort(key=lambda p: p.index)
        self._by_id = {}
        self._order = {}
        for line in self.lines():
            self._order[line.id] = len(self._by_id)
            self._by_id[line.id] = line

    def lines(self) -> list[Line]:
        """Toutes les lignes, dans l'ordre des pages."""
        return [line for page in self.pages for line in page.lines]

    def line(self, line_id: str) -> Line | None:
        """Ligne d'identifiant line_id, ou None."""
        return self._by_id.get(line_id)

    def order(self, line_id: str) -> int:
        """Rang de la ligne dans le document (les lignes inconnues vont à la fin)."""
        return self._order.get(line_id, 1 << 30)


@dataclass
class Entity:
    """Détection renvoyée par un agent IA (texte brut, pas encore localisé)."""

    line_id: str
    text: str
    type: str
    group: str = ""
    confidence: float = 1.0
    source: str = "llm"


@dataclass
class Span:
    """Zone localisée [start, end) dans le texte d'une ligne."""

    line_id: str
    start: int
    end: int
    type: str
    source: str
    value: str | None = None  # valeur servant au calcul du jeton (défaut : texte couvert)
    token: str | None = None
    surrogate: str | None = None  # valeur fictive (mode substitution), posée juste avant le rendu

    @property
    def replacement(self) -> str | None:
        """Texte écrit à la place de l'original : valeur fictive si définie, sinon jeton."""
        return self.surrogate if self.surrogate is not None else self.token

    def text(self, line: Line) -> str:
        """Texte couvert par la zone dans sa ligne."""
        return line.text[self.start:self.end]

    def pseudonym_value(self, line: Line) -> str:
        """Valeur qui sert au calcul du jeton : value si elle est fixée, sinon le texte couvert."""
        return self.value if self.value is not None else self.text(line)
