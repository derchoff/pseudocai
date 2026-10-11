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
    pass


class MappingStore(Protocol):
    def save(self, records: list[MappingRecord]) -> None:
        ...

    def save_trace(self, trace: dict) -> None:
        ...

    def last_trace(self, document_id: str) -> dict | None:
        """Dernière trace d'un document (sert à nettoyer les sorties d'un traitement précédent)."""
        ...


class NullMappingStore:
    def save(self, records: list[MappingRecord]) -> None:
        pass

    def save_trace(self, trace: dict) -> None:
        pass

    def last_trace(self, document_id: str) -> dict | None:
        return None


class JsonFileMappingStore:
    """Développement local uniquement (valeurs chiffrées, mais fichier sur disque)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    def save(self, records: list[MappingRecord]) -> None:
        data = json.loads(self._path.read_text(encoding="utf-8")) if self._path.exists() else {}
        for r in records:
            existing = data.get(r.id)
            if existing and existing["fingerprint"] != r.fingerprint:
                raise TokenCollisionError(r.id)
            data.setdefault(r.id, {"type": r.type, "fingerprint": r.fingerprint, "ciphertext": r.ciphertext})
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save_trace(self, trace: dict) -> None:
        pass

    def last_trace(self, document_id: str) -> dict | None:
        return None


class CosmosMappingStore:
    def __init__(self, database_client, mapping_container: str, trace_container: str):
        self._mappings = database_client.get_container_client(mapping_container)
        self._traces = database_client.get_container_client(trace_container)

    def save(self, records: list[MappingRecord]) -> None:
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
        self._traces.upsert_item(trace)

    def last_trace(self, document_id: str) -> dict | None:
        items = self._traces.query_items(
            query="SELECT TOP 1 * FROM c WHERE c.documentId = @id ORDER BY c.processedAt DESC",
            parameters=[{"name": "@id", "value": document_id}],
            partition_key=document_id,
        )
        return next(iter(items), None)
