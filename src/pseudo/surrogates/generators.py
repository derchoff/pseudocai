"""Générateurs de valeurs fictives réalistes, un par nature de champ.

Chaque générateur reçoit la valeur d'origine (pour en conserver le FORMAT : casse, séparateurs,
découpage) et un générateur aléatoire déjà « graine » de façon déterministe par la fabrique.
Les valeurs produites sont sûres :
- téléphones dans les tranches réservées à la fiction par l'ARCEP ;
- e-mails sur le domaine réservé example.com ;
- NIR fabriqués (clé valide), noms et adresses tirés de listes génériques.
"""

from __future__ import annotations

import random
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Callable

DATA = Path(__file__).resolve().parent / "data"

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
MONTHS_SHORT = ["janv", "févr", "mars", "avr", "mai", "juin", "juil", "août", "sept", "oct", "nov", "déc"]

# Tranches de numéros réservées aux œuvres de fiction (ARCEP), par indicatif d'origine.
FICTION_PREFIXES = {
    "01": "019900", "02": "026191", "03": "035301", "04": "046571", "05": "053649",
    "06": "063998", "07": "063998", "08": "019900", "09": "019900",
}

STREET_TYPES = (
    r"rue|avenue|av\.|bd|boulevard|chemin|all[ée]e|impasse|place|route|quai|cours|square|"
    r"lieu-dit|chauss[ée]e|passage|r[ée]sidence|rond-point|sentier|voie|esplanade|hameau|cit[ée]"
)
STREET_RE = re.compile(
    rf"(?P<num>\d{{1,4}})?(?P<ind>\s*(?:bis|ter|quater)\b)?(?P<sep>\s*,?\s*)"
    rf"(?P<type>\b(?:{STREET_TYPES}))(?P<space>\s+)(?P<name>[^,]+?)(?=\s*,|\s*$|\s+\d{{5}}\b)",
    re.IGNORECASE,
)
POSTCODE_CITY_RE = re.compile(r"(?P<cp>\b\d{5}\b)(?P<space>\s+)(?P<city>[^\d,]+?)(?P<cedex>\s+CEDEX(?:\s+\d+)?)?\s*$",
                              re.IGNORECASE)


# --------------------------------------------------------------------------- listes


@lru_cache(maxsize=None)
def _words(name: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in (DATA / name).read_text(encoding="utf-8").splitlines() if line.strip())


@lru_cache(maxsize=None)
def _communes() -> tuple[tuple[str, str], ...]:
    return tuple(tuple(line.split(";", 1)) for line in _words("communes.csv"))


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


@lru_cache(maxsize=None)
def _gender_index() -> dict[str, str]:
    index = {_fold(n): "f" for n in _words("prenoms_feminins.txt")}
    index.update({_fold(n): "m" for n in _words("prenoms_masculins.txt")})
    return index


# --------------------------------------------------------------------------- mise en forme


def case_like(template: str, new: str) -> str:
    """Reproduit la casse du modèle : MAJUSCULES, minuscules, ou casse des listes (Dupont)."""
    letters = [c for c in template if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return new.upper()
    if letters and all(c.islower() for c in letters):
        return new.lower()
    return new


def fill_pattern(template: str, chars: str, keep: Callable[[str], bool]) -> str:
    """Remplace un à un les caractères du modèle sélectionnés par `keep` ; le reste est conservé."""
    out, it = [], iter(chars)
    for c in template:
        out.append(next(it, c) if keep(c) else c)
    return "".join(out)


def generic(value: str, rng: random.Random) -> str:
    """Même forme que l'original : lettres -> lettres (même casse), chiffres -> chiffres."""
    out = []
    for c in value:
        if c.isdigit():
            out.append(str(rng.randrange(10)))
        elif c.isalpha():
            letter = chr(ord("a") + rng.randrange(26))
            out.append(letter.upper() if c.isupper() else letter)
        else:
            out.append(c)
    return "".join(out)


# --------------------------------------------------------------------------- personnes


def gender_of(first_name: str, hint: str | None, rng: random.Random) -> str:
    first = re.split(r"[-\s]", first_name.strip())[0]
    return _gender_index().get(_fold(first)) or hint or rng.choice("mf")


def first_name(value: str, rng: random.Random, hint: str | None = None) -> str:
    gender = gender_of(value, hint, rng)
    names = _words("prenoms_masculins.txt" if gender == "m" else "prenoms_feminins.txt")
    stripped = value.strip()
    if re.fullmatch(r"[^\W\d_]\.?", stripped):  # initiale : « J. »
        letter = _fold(rng.choice(names))[0].upper()
        return letter + ("." if stripped.endswith(".") else "")
    if "-" in stripped:  # prénom composé
        a, b = rng.sample(names, 2)
        return case_like(stripped, f"{a}-{b}")
    return case_like(stripped, rng.choice(names))


def last_name(value: str, rng: random.Random) -> str:
    names = _words("noms.txt")
    stripped = value.strip()
    new = "-".join(rng.sample(names, 2)) if "-" in stripped else rng.choice(names)
    return case_like(stripped, new)


def person(value: str, rng: random.Random, hint: str | None = None) -> str:
    """Nom complet non découpé (ex. « Jean DUPONT ») : MAJUSCULES = nom, sinon prénom."""
    words = value.split()
    out = [last_name(w, rng) if len(w) > 1 and w.isupper() else first_name(w, rng, hint) for w in words]
    return " ".join(out) if out else generic(value, rng)


# --------------------------------------------------------------------------- dates et NIR

YearMonth = Callable[[int, int], tuple[int, int]]  # (aa, mm d'origine) -> (année, mois fictifs)


def birth_date(value: str, rng: random.Random, year_month: YearMonth) -> str:
    v = value.strip()
    m = re.fullmatch(r"(\d{1,2})(\s?[/.\-]\s?)(\d{1,2})(\s?[/.\-]\s?)(\d{4}|\d{2})", v)
    if m:
        d, s1, mo, s2, y = m.groups()
        year, month = year_month(int(y) % 100, int(mo))
        day = rng.randint(1, 28)
        return (f"{day:0{len(d)}d}{s1}{month:0{len(mo)}d}{s2}"
                f"{year if len(y) == 4 else year % 100:0{len(y)}d}")
    m = re.fullmatch(r"(1er|\d{1,2})(\s+)([^\W\d_]+)(\.?)(\s+)(\d{4})", v)
    if m:
        d, s1, month_name, dot, s2, y = m.groups()
        index = _month_index(month_name)
        if index is not None:
            year, month = year_month(int(y) % 100, index + 1)
            day = rng.randint(1, 28)
            names = MONTHS_SHORT if (dot or len(month_name) <= 4) and _fold(month_name) != "mai" else MONTHS
            new_month = case_like(month_name, names[month - 1])
            new_day = "1er" if day == 1 and d == "1er" else (f"{day:02d}" if len(d) == 2 and d.isdigit() else str(day))
            return f"{new_day}{s1}{new_month}{dot}{s2}{year}"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        year, month = year_month(int(m.group(1)) % 100, int(m.group(2)))
        return f"{year:04d}-{month:02d}-{rng.randint(1, 28):02d}"
    m = re.fullmatch(r"(\d{1,2})([/.\-])(\d{4})", v)
    if m:
        year, month = year_month(int(m.group(3)) % 100, int(m.group(1)))
        return f"{month:0{len(m.group(1))}d}{m.group(2)}{year}"
    return generic(value, rng)


def _month_index(name: str) -> int | None:
    folded = _fold(name).rstrip(".")
    for i, (full, short) in enumerate(zip(MONTHS, MONTHS_SHORT)):
        if folded in (_fold(full), _fold(short)):
            return i
    return None


def nir_key(first13: str) -> str:
    return f"{97 - int(first13) % 97:02d}"


def nir(value: str, rng: random.Random, year_month: YearMonth) -> str:
    """NIR fictif : sexe conservé, année/mois cohérents avec la date de naissance fictive,
    département/commune/ordre tirés au hasard, clé valide, même mise en forme."""
    compact = "".join(c for c in value if c.isalnum()).upper()
    if len(compact) not in (13, 15) or not compact[:5].isdigit():
        return generic(value, rng)
    sex = compact[0] if compact[0] in "12" else rng.choice("12")
    month = int(compact[3:5])
    year, new_month = year_month(int(compact[1:3]), month if 1 <= month <= 12 else 1)
    dept = rng.choice([d for d in range(1, 96) if d != 20])
    first13 = f"{sex}{year % 100:02d}{new_month:02d}{dept:02d}{rng.randint(1, 899):03d}{rng.randint(1, 999):03d}"
    new = first13 + (nir_key(first13) if len(compact) == 15 else "")
    return fill_pattern(value, new, str.isalnum)


# --------------------------------------------------------------------------- coordonnées


def phone(value: str, rng: random.Random) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("0033"):
        national, rebuild = "0" + digits[4:], lambda n: "0033" + n[1:]
    elif digits.startswith("33") and "(0)" in value:
        national, rebuild = digits[2:], lambda n: "33" + n
    elif digits.startswith("33"):
        national, rebuild = "0" + digits[2:], lambda n: "33" + n[1:]
    else:
        national, rebuild = digits, lambda n: n
    prefix = FICTION_PREFIXES.get(national[:2]) if len(national) == 10 and national[0] == "0" else None
    if prefix is None:
        return generic(value, rng)  # numéro étranger ou atypique : même forme, chiffres au hasard
    new = rebuild(prefix + f"{rng.randrange(10000):04d}")
    return fill_pattern(value, new, str.isdigit)


def email(value: str, rng: random.Random) -> str:
    local = value.split("@", 1)[0]
    separator = "." if "." in local else "_" if "_" in local else "-" if "-" in local else ""
    first = _ascii(rng.choice(_words("prenoms_masculins.txt") + _words("prenoms_feminins.txt")))
    last = _ascii(rng.choice(_words("noms.txt")))
    new_local = f"{first}{separator}{last}" if separator else f"{first[0]}{last}"
    if any(c.isdigit() for c in local):
        new_local += f"{rng.randrange(100):02d}"
    return f"{new_local}@example.com"


def _ascii(text: str) -> str:
    return re.sub(r"[^a-z]", "", _fold(text))


def address(value: str, rng: random.Random) -> str:
    """Adresse (ou fragment d'adresse sur une ligne) : numéro, nom de voie, code postal et ville
    remplacés ; mots structurels (rue, Bât., Apt…) conservés ; autres chiffres tirés au hasard."""
    v = value
    replaced = False

    def street(m: re.Match) -> str:
        nonlocal replaced
        replaced = True
        num = m.group("num")
        new_num = str(rng.randint(1, 10 ** len(num) - 1)) if num else ""
        name = case_like(m.group("name"), rng.choice(_words("voies.txt")))
        return f"{new_num}{m.group('ind') or ''}{m.group('sep')}{m.group('type')}{m.group('space')}{name}"

    def city(m: re.Match) -> str:
        nonlocal replaced
        replaced = True
        cp, name = rng.choice(_communes())
        return f"{cp}{m.group('space')}{case_like(m.group('city'), name)}{m.group('cedex') or ''}"

    # Chiffres libres (appartement, boîte postale…) d'abord : même nombre de chiffres, valeur au
    # hasard. Le numéro de voie et le code postal sont ensuite remplacés par les règles ci-dessous
    # (code postal réel cohérent avec sa ville).
    v = re.sub(r"\d+", lambda m: "".join(str(rng.randrange(10)) for _ in m.group()), v)
    v = STREET_RE.sub(street, v)
    v = POSTCODE_CITY_RE.sub(city, v)
    if not replaced:
        if re.fullmatch(r"[^\W\d_][^\d,]*", v.strip()):  # ville seule (« PARIS »)
            return case_like(v, rng.choice(_communes())[1])
        return generic(value, rng)
    return v


def today() -> date:
    return date.today()
