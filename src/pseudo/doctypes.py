"""Catalogue des types de document, lu dans le référentiel Markdown (types-documents.md).

    ## DENTAIRE — Soins dentaires       <- déclare le type DENTAIRE
    - **Dossier** : dentaire            <- sous-dossier de sortie (défaut : type en minuscules)

Convention commune avec les champs : voir catalog.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import CatalogError, parse_sections, raise_if_errors

# Type réservé au code : document que l'agent ne sait pas classer, confiance insuffisante,
# classification en erreur ou impossible (PDF illisible...). Il ne doit pas être déclaré.
UNCLASSIFIED = "NON_CLASSE"

_FOLDER_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class DocTypeCatalogError(CatalogError):
    """Le référentiel des types de document ne respecte pas la convention."""


@dataclass(frozen=True)
class DocTypeDef:
    """Type de document du référentiel : code, libellé et sous-dossier de sortie."""
    type: str
    label: str
    folder: str


@dataclass(frozen=True)
class DocTypeCatalog:
    """Référentiel des types de document (types-documents.md)."""
    doctypes: tuple[DocTypeDef, ...]

    @property
    def types(self) -> tuple[str, ...]:
        """Codes des types déclarés, dans l'ordre du fichier."""
        return tuple(d.type for d in self.doctypes)

    def folder(self, doc_type: str) -> str | None:
        """Sous-dossier d'un type, ou None s'il n'est pas déclaré."""
        for d in self.doctypes:
            if d.type == doc_type:
                return d.folder
        return None

    def types_markdown(self) -> str:
        """Liste injectée dans le prompt de classification à la place de {{LISTE_TYPES}}."""
        return ", ".join(f"`{t}`" for t in (*self.types, UNCLASSIFIED))


def default_folder(doc_type: str) -> str:
    """Sous-dossier par défaut d'un type : DENTAIRE_ORTHO -> dentaire-ortho."""
    return doc_type.lower().replace("_", "-")


def parse_doctypes(markdown: str) -> DocTypeCatalog:
    """Lit types-documents.md et vérifie les dossiers (format, doublons).

    Lève DocTypeCatalogError avec toutes les erreurs trouvées.
    """
    errors: list[str] = []
    doctypes: list[DocTypeDef] = []
    sections = parse_sections(markdown, properties={"dossier"}, kind="type de document", errors=errors)
    for section in sections:
        if section.type == UNCLASSIFIED:
            errors.append(f"ligne {section.lineno} : {UNCLASSIFIED} est réservé au code et ne doit pas être déclaré")
            continue
        folder = default_folder(section.type)
        if "dossier" in section.properties:
            value, lineno = section.properties["dossier"]
            if _FOLDER_RE.match(value):
                folder = value
            else:
                errors.append(
                    f"ligne {lineno} : dossier « {value} » invalide pour {section.type} "
                    "(minuscules, chiffres et « - » uniquement)"
                )
        doctypes.append(DocTypeDef(section.type, section.label, folder))
    folders: dict[str, str] = {}
    for d in doctypes:
        if d.folder in folders:
            errors.append(f"dossier « {d.folder} » utilisé par {folders[d.folder]} et {d.type}")
        folders.setdefault(d.folder, d.type)
    raise_if_errors(errors, "Référentiel des types de document", DocTypeCatalogError)
    return DocTypeCatalog(doctypes=tuple(doctypes))
