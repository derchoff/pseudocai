"""Règles du référentiel pour les JSON de métadonnées de la GED (sections lues par le code).

    ## Champs JSON à ne jamais modifier
    - `DOCUMENT.*`
    - `GED_metadata.ancestors[].id`

    ## Libellés de service
    - **Champs** : `GED_metadata.metadatas.Gestionnaire dédié *`
    - **Commencent par** : Administrateur, Gestion

Notation des chemins : clés séparées par des points ; `[]` = tous les éléments d'un tableau ;
`*` = n'importe quelle clé (en dernière position : tout ce qui est en dessous). Un chemin désigne
aussi tout ce que contient la valeur : `GED_metadata.metadatas.Nature` couvre la liste entière.
Les titres n'ont pas de tiret cadratin : ces sections ne déclarent pas de champ (voir catalog.py).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from fnmatch import fnmatchcase

from .catalog import CatalogError, property_key

KEEP_SECTION = "champs json a ne jamais modifier"
SERVICE_SECTION = "libelles de service"
ARRAY = "[]"

_HEADING_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_PATH_ITEM_RE = re.compile(r"^\s*[-*]\s*`([^`]+)`\s*$")
_PROPERTY_RE = re.compile(r"^\s*[-*]\s*\*\*([^*]+?)\*\*\s*:\s*(.+?)\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

Pattern = tuple[str, ...]


@dataclass(frozen=True)
class JsonRules:
    """Champs conservés tels quels et libellés de service des JSON de métadonnées."""

    keep: tuple[Pattern, ...] = ()
    service_fields: tuple[Pattern, ...] = ()
    service_prefixes: tuple[str, ...] = ()

    def is_kept(self, path: Pattern) -> bool:
        """Vrai si le champ (chemin avec [] pour les tableaux) est dans la liste des champs conservés."""
        return any(_matches(path, p) for p in self.keep)

    def is_service_label(self, path: Pattern, value: str) -> bool:
        """Vrai si le champ est un champ de libellé de service et que sa valeur commence par un des
        mots prévus (mot entier, casse et accents ignorés) : « Gestion Prestations Individuelles »."""
        if not any(_matches(path, p) for p in self.service_fields):
            return False
        folded = _fold(value).lstrip()
        for prefix in self.service_prefixes:
            word = _fold(prefix)
            if word and folded.startswith(word) and not folded[len(word):len(word) + 1].isalnum():
                return True
        return False


def parse_path(text: str) -> Pattern:
    """`GED_metadata.ancestors[].id` -> ("GED_metadata", "ancestors", "[]", "id"). Lève ValueError si vide."""
    segments: list[str] = []
    for part in text.strip().split("."):
        part = part.strip()
        while part.endswith(ARRAY) and part != ARRAY:
            segments.append(part[: -len(ARRAY)].strip())
            part = ARRAY
        segments.append(part)
    if not segments or any(not s for s in segments):
        raise ValueError(text)
    return tuple(segments)


def _matches(path: Pattern, pattern: Pattern) -> bool:
    """Le motif désigne le chemin ou un de ses parents (un champ conservé l'est avec tout son contenu)."""
    if len(path) < len(pattern):
        return False
    for key, pat in zip(path, pattern):
        if pat == ARRAY or "*" not in pat:
            if key != pat:
                return False
        elif not fnmatchcase(key, pat):
            return False
    return True


def _fold(text: str) -> str:
    """Minuscules sans accents."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def parse_json_rules(markdown: str) -> JsonRules:
    """Lit les sections « Champs JSON à ne jamais modifier » et « Libellés de service » (absentes :
    aucune règle). Lève CatalogError si un chemin est mal écrit."""
    keep: list[Pattern] = []
    fields: list[Pattern] = []
    prefixes: list[str] = []
    errors: list[str] = []
    section = None
    for lineno, line in enumerate(_COMMENT_RE.sub(lambda m: "\n" * m.group().count("\n"), markdown).splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            section = property_key(heading.group(1))
            continue
        if section == KEEP_SECTION:
            item = _PATH_ITEM_RE.match(line)
            if item:
                try:
                    keep.append(parse_path(item.group(1)))
                except ValueError:
                    errors.append(f"ligne {lineno} : chemin JSON « {item.group(1)} » invalide")
        elif section == SERVICE_SECTION:
            prop = _PROPERTY_RE.match(line)
            if not prop:
                continue
            key = property_key(prop.group(1))
            if key == "champs":
                for path in re.findall(r"`([^`]+)`", prop.group(2)):
                    try:
                        fields.append(parse_path(path))
                    except ValueError:
                        errors.append(f"ligne {lineno} : chemin JSON « {path} » invalide")
            elif key == "commencent par":
                prefixes += [v.strip() for v in re.split(r"[,;]", prop.group(2)) if v.strip()]
    if errors:
        raise CatalogError("Règles des JSON de métadonnées invalides :\n- " + "\n- ".join(errors))
    return JsonRules(keep=tuple(keep), service_fields=tuple(fields), service_prefixes=tuple(prefixes))
