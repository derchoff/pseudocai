"""Catalogue des champs à pseudonymiser, lu dans le référentiel Markdown (champs-a-pseudonymiser.md).

Le même fichier est envoyé aux agents IA et lu par le code (convention commune : voir catalog.py) :

    ## IBAN — Numéro de compte bancaire        <- déclare le type IBAN
    - **Normalisation** : alphanumerique        <- stratégie de normalisation (défaut : texte)
    - **Substitution** : generique              <- générateur de valeur fictive (défaut : generique)

- Un titre de niveau 2 contenant un tiret cadratin « — » déclare un champ. Le type doit être en
  MAJUSCULES (lettres, chiffres, _). Les autres titres (« ## Règles transverses ») sont ignorés.
- Toute anomalie (type mal écrit, doublon, stratégie inconnue, aucun champ) lève une erreur :
  un champ n'est jamais ignoré silencieusement.
"""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import CatalogError, parse_sections, raise_if_errors
from .surrogates import GENERATORS

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

# Générateur de valeur fictive (mode substitution) quand le référentiel ne l'indique pas.
DEFAULT_SUBSTITUTION = "generique"
BUILTIN_SUBSTITUTIONS = {
    "NOM": "nom",
    "PRENOM": "prenom",
    "ADRESSE": "adresse",
    "NIR": "nir",
    "EMAIL": "email",
    "TELEPHONE": "telephone",
    "DATE_NAISSANCE": "date_naissance",
    "PERSONNE": "personne",  # nom complet détecté par Azure AI Language
}


class FieldCatalogError(CatalogError):
    """Le référentiel des champs ne respecte pas la convention."""


@dataclass(frozen=True)
class FieldDef:
    """Champ à pseudonymiser : code, libellé, normalisation (calcul du jeton) et générateur de valeur fictive."""
    type: str
    label: str
    normalisation: str
    substitution: str = DEFAULT_SUBSTITUTION


@dataclass(frozen=True)
class FieldCatalog:
    """Référentiel des champs à pseudonymiser (champs-a-pseudonymiser.md)."""
    fields: tuple[FieldDef, ...]

    @property
    def types(self) -> tuple[str, ...]:
        """Codes des champs déclarés, dans l'ordre du fichier."""
        return tuple(f.type for f in self.fields)

    def strategy(self, entity_type: str) -> str:
        """Normalisation d'un type : celle du référentiel, sinon celle intégrée au code, sinon « texte »."""
        for f in self.fields:
            if f.type == entity_type:
                return f.normalisation
        return BUILTIN_STRATEGIES.get(entity_type, DEFAULT_STRATEGY)

    def substitution(self, entity_type: str) -> str:
        """Générateur de valeur fictive du type (ligne « Substitution » du référentiel)."""
        for f in self.fields:
            if f.type == entity_type:
                return f.substitution
        return BUILTIN_SUBSTITUTIONS.get(entity_type, DEFAULT_SUBSTITUTION)

    def types_markdown(self) -> str:
        """Liste injectée dans les prompts à la place de {{TYPES}}."""
        return ", ".join(f"`{t}`" for t in self.types)


def parse_fields(markdown: str) -> FieldCatalog:
    """Lit champs-a-pseudonymiser.md et vérifie les lignes Normalisation et Substitution.

    Lève FieldCatalogError avec toutes les erreurs trouvées.
    """
    errors: list[str] = []
    fields: list[FieldDef] = []
    for section in parse_sections(markdown, properties={"normalisation", "substitution"}, kind="champ", errors=errors):
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
        substitution = BUILTIN_SUBSTITUTIONS.get(section.type, DEFAULT_SUBSTITUTION)
        if "substitution" in section.properties:
            value, lineno = section.properties["substitution"]
            value = value.lower()
            if value in GENERATORS:
                substitution = value
            else:
                errors.append(
                    f"ligne {lineno} : substitution « {value} » inconnue pour {section.type} "
                    f"(valeurs possibles : {', '.join(GENERATORS)})"
                )
        fields.append(FieldDef(section.type, section.label, normalisation, substitution))
    raise_if_errors(errors, "Référentiel des champs", FieldCatalogError)
    return FieldCatalog(fields=tuple(fields))
