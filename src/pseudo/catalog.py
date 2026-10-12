"""Lecture générique des référentiels Markdown (champs à pseudonymiser, types de document).

Un même fichier est envoyé aux agents IA et lu par le code, d'où une convention stricte :

    ## TYPE — Libellé                 <- déclare une entrée (TYPE en MAJUSCULES, chiffres, _)
    - **Propriété** : valeur           <- propriété technique lue par le code (ex. Normalisation)
    - **Définition** : ...             <- texte libre destiné au LLM, ignoré par le code

- Seuls les titres de niveau 2 contenant un tiret cadratin « — » déclarent une entrée ; les
  autres titres (« ## Règles transverses ») sont ignorés.
- Les propriétés lues sont celles demandées par l'appelant ; les autres puces sont du texte libre.
- Toute anomalie (titre mal écrit, doublon, propriété répétée, aucune entrée) lève CatalogError :
  une entrée n'est jamais ignorée silencieusement.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_HEADING_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_ENTRY_HEADING_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s+—\s+(\S.*)$")
_PROPERTY_RE = re.compile(r"^\s*[-*]\s*\*\*([^*]+?)\*\*\s*:\s*(.+?)\s*$")


class CatalogError(ValueError):
    """Un référentiel Markdown ne respecte pas la convention."""


@dataclass(frozen=True)
class Section:
    """Section `## TYPE — Libellé` d'un référentiel Markdown, avec ses propriétés `**Clé** : valeur`."""
    type: str
    label: str
    lineno: int
    properties: dict[str, tuple[str, int]] = field(default_factory=dict)  # clé -> (valeur, ligne)


def property_key(name: str) -> str:
    """'Normalisation' / 'normalisation' / 'Dossiér' -> clé comparable sans accents ni casse."""
    folded = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return folded.strip().casefold()


def parse_sections(markdown: str, *, properties: set[str], kind: str, errors: list[str]) -> list[Section]:
    """Découpe le référentiel en sections. Les erreurs de convention sont ajoutées à `errors`.

    `properties` : clés (voir property_key) des puces techniques à lire.
    `kind` : nom de l'entrée dans les messages d'erreur (« champ », « type de document »).
    """
    wanted = {property_key(p) for p in properties}
    sections: list[Section] = []
    current: Section | None = None

    for lineno, raw in enumerate(markdown.splitlines(), start=1):
        heading = _HEADING_RE.match(raw)
        if heading:
            current = None
            title = heading.group(1)
            if "—" not in title:
                continue  # titre de section ordinaire
            match = _ENTRY_HEADING_RE.match(title)
            if not match:
                errors.append(
                    f"ligne {lineno} : « {title} » — le type doit être en MAJUSCULES (A-Z, 0-9, _) "
                    "suivi de « — » et d'un libellé"
                )
                continue
            current = Section(type=match.group(1), label=match.group(2), lineno=lineno)
            sections.append(current)
            continue
        if current is None:
            continue
        prop = _PROPERTY_RE.match(raw)
        if not prop:
            continue
        key = property_key(prop.group(1))
        if key not in wanted:
            continue
        if key in current.properties:
            errors.append(f"ligne {lineno} : plusieurs lignes « {prop.group(1).strip()} » pour {current.type}")
            continue
        current.properties[key] = (prop.group(2).strip().strip("`").strip(), lineno)

    seen: set[str] = set()
    for s in sections:
        if s.type in seen:
            errors.append(f"type {s.type} déclaré plusieurs fois")
        seen.add(s.type)
    if not sections and not errors:
        errors.append(f"aucun {kind} déclaré (titres attendus : « ## TYPE — Libellé »)")
    return sections


def raise_if_errors(errors: list[str], title: str, error_class: type[CatalogError] = CatalogError) -> None:
    """Lève error_class avec la liste des erreurs, s'il y en a."""
    if errors:
        raise error_class(f"{title} invalide :\n- " + "\n- ".join(errors))
