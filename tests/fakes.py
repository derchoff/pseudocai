"""Doublures de test : stockage Blob et traces Cosmos DB en mémoire, enchaînement des étapes."""

from __future__ import annotations

import json
from types import SimpleNamespace

from azure.core.exceptions import ResourceNotFoundError

from pseudo.handler import handle_pdf_job
from pseudo.router import QUEUE_IMAGE, QUEUE_PDF, route_event
from pseudo.runtime import Runtime


class FakeBlobService:
    """Stockage Blob en mémoire : {(container, nom): {"data": bytes, "metadata": dict}}."""

    def __init__(self):
        self.blobs: dict[tuple[str, str], dict] = {}

    def put(self, container: str, name: str, data: bytes) -> None:
        self.blobs[(container, name)] = {"data": data, "metadata": {}}

    def get_blob_client(self, container, name):
        blobs = self.blobs
        key = (container, name)

        def download_blob(offset=None, length=None):
            data = blobs[key]["data"]
            if offset is not None:
                data = data[offset: offset + length if length else None]
            return SimpleNamespace(readall=lambda: data)

        def delete_blob():
            if key not in blobs:
                raise ResourceNotFoundError("absent")
            del blobs[key]

        return SimpleNamespace(
            exists=lambda: key in blobs,
            get_blob_properties=lambda: SimpleNamespace(size=len(blobs[key]["data"])),
            download_blob=download_blob,
            delete_blob=delete_blob,
        )

    def get_container_client(self, container):
        def upload_blob(name, data, overwrite=False, content_settings=None, metadata=None):
            self.blobs[(container, name)] = {"data": data, "metadata": metadata or {}}

        return SimpleNamespace(
            upload_blob=upload_blob,
            get_blob_client=lambda name: self.get_blob_client(container, name),
        )

    def names(self, container):
        return sorted(n for c, n in self.blobs if c == container)

    def data(self, container, name) -> bytes:
        return self.blobs[(container, name)]["data"]


class MemoryStore:
    def __init__(self):
        self.traces = []
        self.records = []

    def save(self, records):
        self.records += records

    def save_trace(self, trace):
        self.traces.append(trace)

    def last_trace(self, document_id):
        mine = [t for t in self.traces if t["documentId"] == document_id]
        return mine[-1] if mine else None

    def save_occurrences(self, document_id, items):
        self.occurrences = [o for o in getattr(self, "occurrences", []) if o["documentId"] != document_id] + items


def event(name: str, container: str = "incoming") -> str:
    return json.dumps({"data": {"url": f"https://st.blob.core.windows.net/{container}/{name}"}})


def run_chain(runtime: Runtime, name: str, prepare_image=None) -> tuple[str, list[str]]:
    """Enchaîne les étapes comme Service Bus le ferait : routeur -> (préparation image) -> pseudonymisation.
    Retourne (statut final, messages transmis entre étapes)."""
    route = route_event(event(name, runtime.settings.incoming_container), runtime)
    messages = []
    if route.queue is None:
        return route.status, messages
    job_json = route.job.to_json()
    messages.append(job_json)
    if route.queue == QUEUE_IMAGE:
        job = prepare_image(job_json, runtime)
        if job is None:
            return "quarantine", messages
        job_json = job.to_json()
        messages.append(job_json)
    assert route.queue in (QUEUE_PDF, QUEUE_IMAGE)
    return handle_pdf_job(job_json, runtime), messages
