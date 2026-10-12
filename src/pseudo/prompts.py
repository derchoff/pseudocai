"""Chargement des prompts (Blob Storage en production, dossier local en développement)."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .doctypes import DocTypeCatalog, parse_doctypes
from .fields import FieldCatalog, parse_fields

CHAMPS_FILE = "champs-a-pseudonymiser.md"
DETECTION_FILE = "agent-detection.md"
VERIFICATION_FILE = "agent-verification.md"
DOCTYPES_FILE = "types-documents.md"
CLASSIFICATION_FILE = "agent-classification.md"
CHAMPS_PLACEHOLDER = "{{CHAMPS}}"
TYPES_PLACEHOLDER = "{{TYPES}}"
DOCTYPES_PLACEHOLDER = "{{TYPES_DOCUMENTS}}"
DOCTYPES_LIST_PLACEHOLDER = "{{LISTE_TYPES}}"
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->\s*", re.DOTALL)


def strip_comments(markdown: str) -> str:
    """Retire les commentaires HTML (notes aux mainteneurs) : ni le code ni les agents ne les voient."""
    return _HTML_COMMENT_RE.sub("", markdown)


class PromptSource(Protocol):
    """Source des fichiers de prompt (dossier local ou container Blob)."""

    def read(self, name: str) -> str:
        """Contenu du fichier name."""
        ...


class LocalPromptSource:
    """Prompts lus dans un dossier local (exécution en ligne de commande, tests)."""

    def __init__(self, directory: str | Path):
        """directory : dossier qui contient les fichiers .md."""
        self._dir = Path(directory)

    def read(self, name: str) -> str:
        """Contenu du fichier name du dossier."""
        return (self._dir / name).read_text(encoding="utf-8")


class BlobPromptSource:
    """Lit les prompts dans un container ; cache invalidé sur changement d'ETag."""

    def __init__(self, container_client, prefix: str, cache_seconds: int = 300):
        """Prompts lus sous prefix dans le container ; vérifiés au plus toutes les cache_seconds."""
        self._container = container_client
        self._prefix = prefix
        self._ttl = cache_seconds
        self._cache: dict[str, tuple[str, str, float]] = {}  # nom -> (etag, texte, vérifié_à)
        self._lock = threading.Lock()

    def read(self, name: str) -> str:
        """Contenu du fichier, depuis le cache tant qu'il est récent et que l'ETag n'a pas changé."""
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(name)
            if cached and now - cached[2] < self._ttl:
                return cached[1]
        blob = self._container.get_blob_client(self._prefix + name)
        etag = blob.get_blob_properties().etag
        if cached and cached[0] == etag:
            text = cached[1]
        else:
            text = blob.download_blob().readall().decode("utf-8")
        with self._lock:
            self._cache[name] = (etag, text, now)
        return text


@dataclass(frozen=True)
class RenderedPrompt:
    """Prompt prêt à envoyer : texte final, empreinte SHA-256 et référentiel utilisé."""
    name: str
    text: str
    sha256: str
    catalog: FieldCatalog | None = None  # champs à pseudonymiser (agents détection / vérification)
    doctypes: DocTypeCatalog | None = None  # types de document (agent de classification)


class PromptRepository:
    """Assemble les prompts des agents à partir des fichiers et des référentiels Markdown."""

    def __init__(self, source: PromptSource):
        """source : d'où lire les fichiers de prompt."""
        self._source = source

    def catalog(self) -> FieldCatalog:
        """Champs déclarés dans le référentiel (lève FieldCatalogError s'il est invalide)."""
        return parse_fields(strip_comments(self._source.read(CHAMPS_FILE)))

    def render(self, name: str) -> RenderedPrompt:
        """Prompt name avec le référentiel des champs inséré ({{CHAMPS}}, {{TYPES}})."""
        champs = strip_comments(self._source.read(CHAMPS_FILE))
        catalog = parse_fields(champs)
        text = strip_comments(self._source.read(name))
        text = text.replace(CHAMPS_PLACEHOLDER, champs).replace(TYPES_PLACEHOLDER, catalog.types_markdown())
        return RenderedPrompt(
            name=name,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            catalog=catalog,
        )

    def doctypes(self) -> DocTypeCatalog:
        """Types de document déclarés (lève DocTypeCatalogError si le référentiel est invalide)."""
        return parse_doctypes(strip_comments(self._source.read(DOCTYPES_FILE)))

    def classification(self) -> RenderedPrompt:
        """Prompt de classification avec le référentiel des types inséré."""
        referential = strip_comments(self._source.read(DOCTYPES_FILE))
        doctypes = parse_doctypes(referential)
        text = strip_comments(self._source.read(CLASSIFICATION_FILE))
        text = text.replace(DOCTYPES_PLACEHOLDER, referential).replace(
            DOCTYPES_LIST_PLACEHOLDER, doctypes.types_markdown()
        )
        return RenderedPrompt(
            name=CLASSIFICATION_FILE,
            text=text,
            sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            doctypes=doctypes,
        )

    def detection(self) -> RenderedPrompt:
        """Prompt de l'agent de détection."""
        return self.render(DETECTION_FILE)

    def verification(self) -> RenderedPrompt:
        """Prompt de l'agent de vérification."""
        return self.render(VERIFICATION_FILE)
