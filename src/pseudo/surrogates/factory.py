"""Fabrique de valeurs fictives : déterministe, cohérente, et jamais égale à une vraie valeur.

- Déterministe : le tirage est « graine » par HMAC(clé, générateur | valeur normalisée). Une même
  valeur d'origine reçoit donc toujours la même valeur fictive, dans tous les documents (comme
  les jetons), et la valeur fictive ne permet pas de retrouver l'original sans la clé.
- Date de naissance et NIR cohérents : l'année et le mois fictifs sont calculés à partir de
  l'année (2 chiffres) et du mois d'origine seulement, avec la même graine pour les deux champs.
  La date fictive et le NIR fictif d'une même personne correspondent donc toujours, même s'ils
  apparaissent dans des documents différents.
- Jamais une vraie valeur : la valeur fictive doit différer de l'originale et de toutes les
  autres valeurs d'origine du document ; sinon un autre tirage est fait.
"""

from __future__ import annotations

import hashlib
import hmac
import random
from typing import Callable

from . import generators as g

GENERATORS = ("nom", "prenom", "personne", "date_naissance", "nir", "telephone", "email", "adresse", "generique")
MAX_ATTEMPTS = 25
OLDEST_BIRTH_YEAR = 1925


class SurrogateFactory:
    def __init__(self, hmac_key: bytes, current_year: int | None = None):
        if len(hmac_key) < 32:
            raise ValueError("La clé HMAC doit faire au moins 256 bits")
        self._key = hmac_key
        self._current_year = current_year or g.today().year

    def _rng(self, *parts: object) -> random.Random:
        digest = hmac.new(self._key, "|".join(map(str, parts)).encode("utf-8"), hashlib.sha256).digest()
        return random.Random(int.from_bytes(digest, "big"))

    def birth_year_month(self, yy: int, month: int, salt: int = 0) -> tuple[int, int]:
        """(aa, mm) d'origine -> (année complète, mois) fictifs, entre 1925 et l'an dernier.
        Le sel ne sert qu'aux nouveaux tirages après collision (cas rare)."""
        rng = self._rng("annee-mois", f"{yy:02d}", f"{month:02d}", salt)
        return rng.randint(OLDEST_BIRTH_YEAR, self._current_year - 1), rng.randint(1, 12)

    def generate(self, generator: str, value: str, *, key: str, attempt: int = 0, gender_hint: str | None = None) -> str:
        rng = self._rng(generator, key, attempt)
        if generator == "nom":
            return g.last_name(value, rng)
        if generator == "prenom":
            return g.first_name(value, rng, gender_hint)
        if generator == "personne":
            return g.person(value, rng, gender_hint)
        if generator == "date_naissance":
            return g.birth_date(value, rng, self._year_month(attempt))
        if generator == "nir":
            return g.nir(value, rng, self._year_month(attempt))
        if generator == "telephone":
            return g.phone(value, rng)
        if generator == "email":
            return g.email(value, rng)
        if generator == "adresse":
            return g.address(value, rng)
        return g.generic(value, rng)

    def _year_month(self, attempt: int) -> g.YearMonth:
        return lambda yy, mm: self.birth_year_month(yy, mm, salt=attempt)

    def unique(
        self,
        generator: str,
        value: str,
        *,
        key: str,
        forbidden: set[str],
        normalize: Callable[[str], str],
        gender_hint: str | None = None,
    ) -> str:
        """Valeur fictive dont la forme normalisée n'appartient pas à `forbidden`
        (valeur d'origine et autres vraies valeurs du document)."""
        for attempt in range(MAX_ATTEMPTS):
            candidate = self.generate(generator, value, key=key, attempt=attempt, gender_hint=gender_hint)
            if normalize(candidate) not in forbidden and candidate.strip():
                return candidate
        return g.generic(value, self._rng("secours", key))
