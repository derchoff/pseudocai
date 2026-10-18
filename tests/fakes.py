"""Doublures de test : stockage Blob et traces Cosmos DB en mémoire, enchaînement des étapes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

from pseudo.handler import handle_pdf_job
from pseudo.router import QUEUE_IMAGE, QUEUE_MAIL, QUEUE_PDF, route_event
from pseudo.runtime import Runtime


# Date de dépose de tous les fichiers des tests : 14 mars 2026, 9 h 26 UTC = 10 h 26 à Paris.
DEPOSITED = datetime(2026, 3, 14, 9, 26, tzinfo=timezone.utc)
DEPOSIT = "year=2026/month=03/day=14/hour=10"  # dossiers de sortie correspondants (Europe/Paris)


class FakeBlobService:
    """Stockage Blob en mémoire : {(container, nom): {"data": bytes, "metadata": dict}}."""

    def __init__(self):
        """Blobs en mémoire : (container, nom) -> contenu et métadonnées."""
        self.blobs: dict[tuple[str, str], dict] = {}

    def put(self, container: str, name: str, data: bytes, deposited: datetime = DEPOSITED) -> None:
        """Dépose un fichier (date de dépose : DEPOSITED par défaut)."""
        self.blobs[(container, name)] = {"data": data, "metadata": {}, "deposited": deposited}

    def get_blob_client(self, container, name):
        """Client d'un blob : exists, propriétés, téléchargement, suppression."""
        blobs = self.blobs
        key = (container, name)

        def download_blob(offset=None, length=None):
            """Contenu du blob (ou une partie avec offset et length)."""
            data = blobs[key]["data"]
            if offset is not None:
                data = data[offset: offset + length if length else None]
            return SimpleNamespace(readall=lambda: data)

        def delete_blob():
            """Supprime le blob ; lève ResourceNotFoundError s'il est absent."""
            if key not in blobs:
                raise ResourceNotFoundError("absent")
            del blobs[key]

        return SimpleNamespace(
            exists=lambda: key in blobs,
            get_blob_properties=lambda: SimpleNamespace(size=len(blobs[key]["data"]), last_modified=blobs[key].get("deposited", DEPOSITED)),
            download_blob=download_blob,
            delete_blob=delete_blob,
        )

    def get_container_client(self, container):
        """Client d'un container : upload_blob, list_blobs et get_blob_client."""
        def upload_blob(name, data, overwrite=False, content_settings=None, metadata=None):
            """Écrit un blob avec ses métadonnées ; sans overwrite, refuse s'il existe déjà."""
            if not overwrite and (container, name) in self.blobs:
                raise ResourceExistsError("existe")
            self.blobs[(container, name)] = {"data": data, "metadata": metadata or {}}

        def list_blobs(name_starts_with=""):
            """Blobs du container dont le nom commence par le préfixe."""
            return [SimpleNamespace(name=n) for c, n in list(self.blobs) if c == container and n.startswith(name_starts_with)]

        return SimpleNamespace(
            upload_blob=upload_blob,
            list_blobs=list_blobs,
            get_blob_client=lambda name: self.get_blob_client(container, name),
        )

    def names(self, container):
        """Noms des blobs du container, triés."""
        return sorted(n for c, n in self.blobs if c == container)

    def data(self, container, name) -> bytes:
        """Contenu d'un blob."""
        return self.blobs[(container, name)]["data"]


class MemoryStore:
    """Stockage des correspondances, traces et occurrences en mémoire."""

    def __init__(self):
        """Listes vides de traces et de correspondances."""
        self.traces = []
        self.records = []

    def save(self, records):
        """Ajoute les correspondances."""
        self.records += records

    def save_trace(self, trace):
        """Ajoute la trace."""
        self.traces.append(trace)

    def last_trace(self, document_id):
        """Dernière trace du document, ou None."""
        mine = [t for t in self.traces if t["documentId"] == document_id]
        return mine[-1] if mine else None

    def save_occurrences(self, document_id, items):
        """Remplace les occurrences du document."""
        self.occurrences = [o for o in getattr(self, "occurrences", []) if o["documentId"] != document_id] + items


def event(name: str, container: str = "incoming", host: str = "st.blob.core.windows.net") -> str:
    """Événement Event Grid BlobCreated (JSON) pour le fichier name du compte host."""
    return json.dumps({"data": {"url": f"https://{host}/{container}/{name}"}})


def run_chain(runtime: Runtime, name: str, prepare_image=None) -> tuple[str, list[str]]:
    """Enchaîne les étapes comme Service Bus le ferait : routeur -> (préparation image) -> pseudonymisation.
    Retourne (statut final, messages transmis entre étapes)."""
    source_url = runtime.settings.source_storage_account_url
    host = urlparse(source_url).hostname if source_url else "st.blob.core.windows.net"
    route = route_event(event(name, runtime.settings.incoming_container, host), runtime)
    messages = []
    if route.queue is None:
        return route.status, messages
    job_json = route.job.to_json()
    messages.append(job_json)
    if route.queue == QUEUE_MAIL:
        return _run_mail(runtime, job_json, prepare_image, messages)
    if route.queue == QUEUE_IMAGE:
        job = prepare_image(job_json, runtime)
        if job is None:
            return "quarantine", messages
        job_json = job.to_json()
        messages.append(job_json)
    assert route.queue in (QUEUE_PDF, QUEUE_IMAGE)
    return handle_pdf_job(job_json, runtime), messages


def _run_mail(runtime: Runtime, job_json: str, prepare_image, messages: list[str]) -> tuple[str, list[str]]:
    """Mail : découpage, puis chaque pièce jointe (préparation si image) ; statut = trace du mail."""
    from pseudo.mails.split import handle_mail_job
    from pseudo.router import QUEUE_TEXT
    from pseudo.text_jobs import handle_text_job

    for queue, child in handle_mail_job(job_json, runtime):
        messages.append(child)
        if queue == QUEUE_TEXT:
            handle_text_job(child, runtime)
            continue
        if queue == QUEUE_IMAGE:
            child = prepare_image(child, runtime).to_json()
            messages.append(child)
        handle_pdf_job(child, runtime)
    return runtime.mapping_store.traces[-1]["status"], messages
