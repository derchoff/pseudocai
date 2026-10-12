"""Persistance de la table de correspondance jeton -> valeur chiffrée, et des traces de traitement."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .pseudonymizer import MappingRecord

log = logging.getLogger(__name__)


class TokenCollisionError(RuntimeError):
    """Deux valeurs différentes ont produit le même jeton : augmenter TOKEN_LENGTH."""
    pass


class MappingStore(Protocol):
    """Stockage des correspondances chiffrées, des traces et des occurrences."""

    def save(self, records: list[MappingRecord]) -> None:
        """Enregistre les correspondances jeton -> valeur chiffrée (sans écraser l'existant)."""
        ...

    def save_trace(self, trace: dict) -> None:
        """Enregistre la trace d'un traitement."""
        ...

    def last_trace(self, document_id: str) -> dict | None:
        """Dernière trace d'un document (sert à nettoyer les sorties d'un traitement précédent)."""
        ...

    def save_occurrences(self, document_id: str, items: list[dict]) -> None:
        """Remplace les occurrences (zones remplacées) d'un document publié."""
        ...


class NullMappingStore:
    """Stockage qui n'enregistre rien (exécution locale sans persistance)."""

    def save(self, records: list[MappingRecord]) -> None:
        """Ne fait rien."""
        pass

    def save_trace(self, trace: dict) -> None:
        """Ne fait rien."""
        pass

    def last_trace(self, document_id: str) -> dict | None:
        """Aucune trace connue."""
        return None

    def save_occurrences(self, document_id: str, items: list[dict]) -> None:
        """Ne fait rien."""
        pass


class JsonFileMappingStore:
    """Développement local uniquement (valeurs chiffrées, mais fichier sur disque)."""

    def __init__(self, path: str | Path):
        """path : fichier JSON des correspondances, créé au premier enregistrement."""
        self._path = Path(path)

    def save(self, records: list[MappingRecord]) -> None:
        """Ajoute les correspondances au fichier ; lève TokenCollisionError en cas de collision."""
        data = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        for r in records:
            existing = data.get(r.id)
            if existing and existing["fingerprint"] != r.fingerprint:
                raise TokenCollisionError(r.id)
            data.setdefault(r.id, {"type": r.type, "fingerprint": r.fingerprint, "ciphertext": r.ciphertext})
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save_trace(self, trace: dict) -> None:
        """Les traces ne sont pas conservées en local."""
        pass

    def last_trace(self, document_id: str) -> dict | None:
        """Aucune trace en local."""
        return None

    def save_occurrences(self, document_id: str, items: list[dict]) -> None:
        """Les occurrences ne sont pas conservées en local."""
        pass


class CosmosMappingStore:
    """Stockage Cosmos DB : containers mappings, traces et occurrences."""

    def __init__(self, database_client, mapping_container: str, trace_container: str, occurrence_container: str):
        """database_client : base Cosmos DB ; les noms désignent ses trois containers."""
        self._mappings = database_client.get_container_client(mapping_container)
        self._traces = database_client.get_container_client(trace_container)
        self._occurrences = database_client.get_container_client(occurrence_container)

    def save(self, records: list[MappingRecord]) -> None:
        """Crée les correspondances absentes ; lève TokenCollisionError si un jeton existe pour une autre valeur."""
        from azure.cosmos.exceptions import CosmosResourceExistsError

        now = datetime.now(timezone.utc).isoformat()
        for r in records:
            body = {
                "id": r.id,
                "type": r.type,
                "fingerprint": r.fingerprint,
                "ciphertext": r.ciphertext,
                "createdAt": now,
            }
            try:
                self._mappings.create_item(body)
            except CosmosResourceExistsError:
                existing = self._mappings.read_item(item=r.id, partition_key=r.id)
                if existing.get("fingerprint") != r.fingerprint:
                    # Deux valeurs différentes partagent le même jeton tronqué : augmenter TOKEN_LENGTH.
                    raise TokenCollisionError(r.id)

    def save_trace(self, trace: dict) -> None:
        """Écrit (ou remplace) la trace d'un traitement."""
        self._traces.upsert_item(trace)

    def last_trace(self, document_id: str) -> dict | None:
        """Trace la plus récente d'un document, ou None."""
        items = self._traces.query_items(
            query="SELECT TOP 1 * FROM c WHERE c.documentId = @id ORDER BY c.processedAt DESC",
            parameters=[{"name": "@id", "value": document_id}],
            partition_key=document_id,
        )
        return next(iter(items), None)

    def save_occurrences(self, document_id: str, items: list[dict]) -> None:
        """Les occurrences d'un traitement précédent du même document sont supprimées d'abord :
        elles décrivent toujours la version publiée en cours."""
        previous = self._occurrences.query_items(
            query="SELECT c.id FROM c WHERE c.documentId = @id",
            parameters=[{"name": "@id", "value": document_id}],
            partition_key=document_id,
        )
        for item in previous:
            self._occurrences.delete_item(item=item["id"], partition_key=document_id)
        for item in items:
            self._occurrences.upsert_item(item)
