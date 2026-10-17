"""Génération des jetons (HMAC déterministe) et chiffrement des valeurs d'origine (AES-GCM)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import unicodedata
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .datewords import parse_letter_date
from .fields import BUILTIN_STRATEGIES, DEFAULT_STRATEGY

_MONTHS = {
    "janv": 1, "janvier": 1, "fev": 2, "fevrier": 2, "mars": 3, "avr": 4, "avril": 4, "mai": 5,
    "juin": 6, "juil": 7, "juillet": 7, "aout": 8, "sept": 9, "septembre": 9, "oct": 10,
    "octobre": 10, "nov": 11, "novembre": 11, "dec": 12, "decembre": 12,
}


def _strip_accents(value: str) -> str:
    """Retire les accents (é -> e)."""
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def normalize(strategy: str, value: str) -> str:
    """Forme canonique selon la stratégie du champ : une même donnée écrite différemment
    (espaces, ponctuation, casse, format de date...) produit le même jeton."""
    v = value.strip()
    if strategy == "telephone":
        digits = re.sub(r"\D", "", v.replace("(0)", ""))
        if digits.startswith("0033"):
            digits = "0" + digits[4:]
        elif digits.startswith("33") and len(digits) == 11:
            digits = "0" + digits[2:]
        return digits
    if strategy == "chiffres":
        return re.sub(r"\D", "", v)
    if strategy == "alphanumerique":
        return re.sub(r"[^0-9A-Z]", "", _strip_accents(v).upper())
    if strategy == "email":
        return re.sub(r"\s+", "", v).lower()
    if strategy == "date":
        iso = _normalize_date(v)
        if iso:
            return iso
    folded = _strip_accents(v).casefold()
    folded = re.sub(r"[\s\-'’.,]+", " ", folded)
    return folded.strip()


def _normalize_date(value: str) -> str | None:
    """Date ramenée à AAAA-MM-JJ (12/03/1985, 12 mars 1985, 1985-03-12, vingt-trois mars mil neuf
    cent cinquante-deux), ou None si non reconnue.

    Une année sur 2 chiffres donne « xxAA ».
    """
    v = _strip_accents(value).lower().replace("1er", "1")
    m = re.fullmatch(r"(\d{1,2})\s?[/.\-]\s?(\d{1,2})\s?[/.\-]\s?(\d{2}|\d{4})", v)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{y if len(y) == 4 else 'xx' + y}-{mo:02d}-{d:02d}"
    m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})", v)
    if m and m.group(2) in _MONTHS:
        return f"{m.group(3)}-{_MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        return v
    letters = parse_letter_date(value)  # « vingt-trois mars mil neuf cent cinquante-deux »
    if letters:
        return f"{letters.year:04d}-{letters.month:02d}-{letters.day:02d}"
    return None


class Pseudonymizer:
    """Calcule les jetons : HMAC-SHA256 de la valeur normalisée, tronqué."""

    def __init__(self, hmac_key: bytes, token_length: int = 10):
        """hmac_key : clé secrète (256 bits minimum) ; token_length : caractères hexadécimaux du jeton."""
        if len(hmac_key) < 32:
            raise ValueError("La clé HMAC doit faire au moins 256 bits")
        self._key = hmac_key
        self._length = token_length

    def fingerprint(self, entity_type: str, value: str, strategy: str | None = None) -> str:
        """Empreinte HMAC complète de la valeur normalisée (sert à détecter les collisions de jetons)."""
        strategy = strategy or BUILTIN_STRATEGIES.get(entity_type, DEFAULT_STRATEGY)
        message = f"{entity_type}|{normalize(strategy, value)}".encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def token(self, entity_type: str, value: str, strategy: str | None = None) -> str:
        """Jeton de la valeur, ex. [NOM_3FA94C0B21]. Même valeur normalisée -> même jeton."""
        fingerprint = self.fingerprint(entity_type, value, strategy)
        return f"[{entity_type}_{fingerprint[: self._length].upper()}]"


class ValueCipher:
    """Chiffre la valeur d'origine ; le jeton sert de données associées (AAD)."""

    def __init__(self, key: bytes):
        """key : clé AES-256 (32 octets)."""
        if len(key) != 32:
            raise ValueError("La clé de chiffrement doit faire 256 bits")
        self._aes = AESGCM(key)

    def encrypt(self, token: str, value: str) -> str:
        """Chiffre la valeur d'origine (AES-256-GCM, lié au jeton). Renvoie nonce + chiffré en base64."""
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, value.encode("utf-8"), token.encode("utf-8"))
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str, payload: str) -> str:
        """Déchiffre une valeur ; échoue si le jeton ne correspond pas."""
        raw = base64.b64decode(payload)
        return self._aes.decrypt(raw[:12], raw[12:], token.encode("utf-8")).decode("utf-8")


@dataclass
class MappingRecord:
    """Correspondance stockée : jeton, type, empreinte et valeur d'origine chiffrée."""
    token: str
    type: str
    fingerprint: str
    ciphertext: str

    @property
    def id(self) -> str:
        """Jeton sans crochets (identifiant du document Cosmos DB)."""
        return self.token.strip("[]")
