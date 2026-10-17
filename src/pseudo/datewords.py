"""Dates écrites en toutes lettres, comme dans les actes d'état civil.

    « vingt-trois mars mil neuf cent cinquante-deux », « premier janvier deux mille »,
    « 23 mars mil neuf cent cinquante-deux » (jour en chiffres, année en lettres)

Lecture (pour le jeton : même date en lettres ou en chiffres -> même valeur normalisée) et écriture
(pour la valeur fictive : une autre date, écrite de la même façon).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_UNITS = {
    "zero": 0, "un": 1, "une": 1, "premier": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "treize": 13, "quatorze": 14,
    "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60,
}
_ONES = ["", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze",
         "treize", "quatorze", "quinze", "seize"]
_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}

_MONTH_RE = (r"janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|"
             r"d[ée]cembre")
_DATE_RE = re.compile(rf"^(?P<day>.+?)(?P<s1>\s+)(?P<month>{_MONTH_RE})(?P<s2>\s+)(?P<year>.+)$", re.IGNORECASE)
_MONTHS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre",
           "novembre", "decembre"]


def _fold(text: str) -> str:
    """Minuscules sans accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def words_to_int(text: str) -> int | None:
    """Nombre écrit en lettres (« mil neuf cent quatre-vingt-cinq » -> 1985), ou None."""
    words = [w for w in re.split(r"[\s\-]+", _fold(text)) if w and w != "et"]
    if not words:
        return None
    total = current = 0
    for w in words:
        if w in ("mil", "mille"):
            total += (current or 1) * 1000
            current = 0
        elif w in ("cent", "cents"):
            current = (current or 1) * 100
        elif w in ("vingt", "vingts") and current % 100 == 4:
            current += 76  # quatre-vingt : 4 -> 80
        elif w in _UNITS:
            current += _UNITS[w]
        else:
            return None
    return total + current


def int_to_words(n: int, mil: bool = False, year: bool = False) -> str:
    """Nombre en lettres, orthographe traditionnelle (« quatre-vingt-cinq », « vingt et un »).
    mil : « mil » au lieu de « mille » pour les années 1001 à 1999 (usage des actes) ;
    year : « cent » et « vingt » invariables, comme dans une date (« mil neuf cent quatre-vingt »)."""
    if n == 0:
        return "zéro"
    parts = []
    thousands, rest = divmod(n, 1000)
    if thousands:
        word = "mil" if mil and thousands == 1 and rest else "mille"
        parts.append(word if thousands == 1 else f"{_below_1000(thousands, year)} mille")
    if rest:
        parts.append(_below_1000(rest, year))
    return " ".join(parts)


def _below_1000(n: int, invariable: bool = False) -> str:
    """Nombre de 1 à 999 en lettres (invariable : sans pluriel de « cent » et « vingt »)."""
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        plural = "s" if not rest and not invariable else ""
        parts.append("cent" if hundreds == 1 else f"{_ONES[hundreds]} cent{plural}")
    if rest:
        parts.append(_below_100(rest, invariable))
    return " ".join(parts)


def _below_100(n: int, invariable: bool = False) -> str:
    """Nombre de 1 à 99 en lettres."""
    if n <= 16:
        return _ONES[n]
    if n < 20:
        return f"dix-{_ONES[n - 10]}"
    if n < 70:
        tens, unit = divmod(n, 10)
        if not unit:
            return _TENS[tens]
        return f"{_TENS[tens]} et un" if unit == 1 else f"{_TENS[tens]}-{_ONES[unit]}"
    if n < 80:
        return "soixante et onze" if n == 71 else f"soixante-{_below_100(n - 60)}"
    if n == 80:
        return "quatre-vingt" if invariable else "quatre-vingts"
    return f"quatre-vingt-{_below_100(n - 80)}"


@dataclass
class LetterDate:
    """Date lue dans un texte : valeurs et forme de chaque partie (pour écrire une date semblable)."""
    day: int
    month: int
    year: int
    day_text: str
    sep1: str
    month_text: str
    sep2: str
    year_text: str

    @property
    def day_in_words(self) -> bool:
        """Jour écrit en lettres (« vingt-trois », « premier »)."""
        return not any(c.isdigit() for c in self.day_text)

    @property
    def year_in_words(self) -> bool:
        """Année écrite en lettres."""
        return not any(c.isdigit() for c in self.year_text)


def parse_letter_date(value: str) -> LetterDate | None:
    """Date dont le jour ou l'année au moins est en lettres, ou None (une date tout en chiffres est
    traitée ailleurs)."""
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    day_text, year_text = m.group("day"), m.group("year")
    day = _number(day_text)
    year = _number(year_text)
    if day is None or year is None or not 1 <= day <= 31 or not 1000 <= year <= 2999:
        return None
    letters = LetterDate(day, _MONTHS.index(_fold(m.group("month"))) + 1, year, day_text, m.group("s1"),
                         m.group("month"), m.group("s2"), year_text)
    return letters if letters.day_in_words or letters.year_in_words else None


def _number(text: str) -> int | None:
    """Nombre en chiffres (« 23 », « 1er ») ou en lettres."""
    t = text.strip()
    if re.fullmatch(r"\d{1,4}", t):
        return int(t)
    if _fold(t) == "1er":
        return 1
    return words_to_int(t)
