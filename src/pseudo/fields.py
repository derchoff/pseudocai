"""Catalogue des champs à pseudonymiser, lu dans le référentiel Markdown (champs-a-pseudonymiser.md).

Le même fichier est envoyé aux agents IA et lu par le code (convention commune : voir catalog.py) :

    ## IBAN — Numéro de compte bancaire        <- déclare le type IBAN
    - **Normalisation** : alphanumerique        <- stratégie de normalisation (défaut : texte)

- Un titre de niveau 2 contenant un tiret cadratin « — » déclare un champ. Le type doit être en
  MAJUSCULES (lettres, chiffres, _). Les autres titres (« ## Règles transverses ») sont ignorés.
- Toute anomalie (type mal écrit, doublon, stratégie inconnue, aucun champ) lève une erreur :
  un champ n'est jamais ignoré silencieusement.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import CatalogError, parse_sections, raise_if_errors

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


class FieldCatalogError(CatalogError):
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
    errors: list[str] = []
    fields: list[FieldDef] = []
    for section in parse_sections(markdown, properties={"normalisation"}, kind="champ", errors=errors):
        normalisation = DEFAULT_STRATEGY
        if "normalisation" in section.properties:
            value, lineno = section.properties["normalisation"]
            value = value.lower()
            if value in STRATEGIES:
                normalisation = value
            else:
                errors.append(
                    f"ligne {lineno} : normalisation « {value} » inconnue pour {section.type} "
                    f"(valeurs possibles : {', '.join(STRATEGIES)})"
                )
        fields.append(FieldDef(section.type, section.label, normalisation))
    raise_if_errors(errors, "Référentiel des champs", FieldCatalogError)
    return FieldCatalog(fields=tuple(fields))
