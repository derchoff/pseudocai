"""Catalogue des champs à pseudonymiser, lu dans le référentiel Markdown (champs-a-pseudonymiser.md).

Le même fichier est envoyé aux agents IA et lu par le code, d'où une convention stricte :

    ## IBAN — Numéro de compte bancaire        <- déclare le type IBAN
    - **Normalisation** : alphanumerique        <- stratégie de normalisation (défaut : texte)

- Un titre de niveau 2 contenant un tiret cadratin « — » déclare un champ. Le type doit être en
  MAJUSCULES (lettres, chiffres, _). Les autres titres (« ## Règles transverses ») sont ignorés.
- Toute anomalie (type mal écrit, doublon, stratégie inconnue, aucun champ) lève une erreur :
  un champ n'est jamais ignoré silencieusement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STRATEGIES = ("texte", "chiffres", "alphanumerique", "email", "telephone", "date")
DEFAULT_STRATEGY = "texte"

# Stratégies des types produits par les filets de sécurité (regex, Azure AI Language) quand le
# catalogue ne les déclare pas, et des tests qui n'utilisent pas de catalogue.
BUILTIN_STRATEGIES = {
    "NIR": "alphanumerique",
    "EMAIL": "email",
    "TELEPHONE": "telephone",
    "DATE_NAISSANCE": "date",
}

_HEADING_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_FIELD_HEADING_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s+—\s+(\S.*)$")
_NORMALISATION_RE = re.compile(r"^\s*[-*]\s*\*\*Normalisation\*\*\s*:\s*`?([^`\s]+)`?\s*$", re.IGNORECASE)


class FieldCatalogError(ValueError):
    """Le référentiel des champs ne respecte pas la convention."""


@dataclass(frozen=True)
class FieldDef:
    type: str
    label: str
    normalisation: str


@dataclass(frozen=True)
class FieldCatalog:
    fields: tuple[FieldDef, ...]

    @property
    def types(self) -> tuple[str, ...]:
        return tuple(f.type for f in self.fields)

    def strategy(self, entity_type: str) -> str:
        for f in self.fields:
            if f.type == entity_type:
                return f.normalisation
        return BUILTIN_STRATEGIES.get(entity_type, DEFAULT_STRATEGY)

    def types_markdown(self) -> str:
        """Liste injectée dans les prompts à la place de {{TYPES}}."""
        return ", ".join(f"`{t}`" for t in self.types)


def parse_fields(markdown: str) -> FieldCatalog:
    fields: list[FieldDef] = []
    errors: list[str] = []
    current: dict | None = None

    def close(section: dict | None) -> None:
        if section is not None:
            fields.append(FieldDef(section["type"], section["label"], section["normalisation"]))

    for lineno, raw in enumerate(markdown.splitlines(), start=1):
        heading = _HEADING_RE.match(raw)
        if heading:
            close(current)
            current = None
            title = heading.group(1)
            if "—" not in title:
                continue  # titre de section ordinaire
            match = _FIELD_HEADING_RE.match(title)
            if not match:
                errors.append(
                    f"ligne {lineno} : « {title} » — le type doit être en MAJUSCULES (A-Z, 0-9, _) "
                    "suivi de « — » et d'un libellé"
                )
                continue
            current = {"type": match.group(1), "label": match.group(2), "normalisation": DEFAULT_STRATEGY,
                       "seen_normalisation": False}
            continue
        if current is None:
            continue
        norm = _NORMALISATION_RE.match(raw)
        if norm:
            value = norm.group(1).strip().lower()
            if current["seen_normalisation"]:
                errors.append(f"ligne {lineno} : plusieurs lignes « Normalisation » pour {current['type']}")
            elif value not in STRATEGIES:
                errors.append(
                    f"ligne {lineno} : normalisation « {value} » inconnue pour {current['type']} "
                    f"(valeurs possibles : {', '.join(STRATEGIES)})"
                )
            else:
                current["normalisation"] = value
            current["seen_normalisation"] = True
    close(current)

    seen: set[str] = set()
    for f in fields:
        if f.type in seen:
            errors.append(f"type {f.type} déclaré plusieurs fois")
        seen.add(f.type)
    if not fields and not errors:
        errors.append("aucun champ déclaré (titres attendus : « ## TYPE — Libellé »)")
    if errors:
        raise FieldCatalogError("Référentiel des champs invalide :\n- " + "\n- ".join(errors))
    return FieldCatalog(fields=tuple(fields))
