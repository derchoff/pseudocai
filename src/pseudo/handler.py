"""Traitement d'un événement BlobCreated : lecture, pipeline, écriture des sorties."""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from .pipeline import run_pipeline
from .runtime import Runtime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlobRef:
    url: str
    container: str
    name: str

    @property
    def doc_ref(self) -> str:
        """Identifiant pseudonyme du document pour les logs (le nom de fichier peut contenir un nom)."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]


def parse_event(body: bytes | str) -> BlobRef:
    data = json.loads(body)
    if isinstance(data, list):  # Event Grid peut livrer un tableau d'événements
        data = data[0]
    url = data["data"]["url"]
    path = unquote(urlparse(url).path).lstrip("/")
    container, _, name = path.partition("/")
    return BlobRef(url=url, container=container, name=name)


def output_names(blob_name: str) -> dict[str, str]:
    stem, _ = posixpath.splitext(blob_name)
    return {
        "pdf": blob_name,
        "text": f"{stem}.pseudonymise.txt",
        "report": f"{stem}.rapport.json",
        "reason": f"{blob_name}.motif.json",
    }


def handle_event(body: bytes | str, runtime: Runtime) -> str:
    settings = runtime.settings
    blob = parse_event(body)
    if blob.container != settings.input_container:
        log.warning("Événement ignoré (container %s)", blob.container)
        return "ignored"
    if not blob.name.lower().endswith(".pdf"):
        log.info("Fichier non PDF ignoré doc=%s", blob.doc_ref)
        return "ignored"

    service = runtime.blob_service
    source = service.get_blob_client(settings.input_container, blob.name)
    if not source.exists():
        log.warning("Blob introuvable (déjà traité ou supprimé) doc=%s", blob.doc_ref)
        return "missing"
    props = source.get_blob_properties()
    if props.size > settings.max_file_mb * 1024 * 1024:
        return _quarantine(runtime, blob, source.download_blob().readall(), ["fichier_trop_volumineux"], {})

    pdf_bytes = source.download_blob().readall()
    log.info("Traitement doc=%s taille=%d", blob.doc_ref, len(pdf_bytes))
    result = run_pipeline(pdf_bytes, settings, runtime.services)

    if result.status != "ok":
        return _quarantine(runtime, blob, pdf_bytes, result.reasons, result.report)

    names = output_names(blob.name)
    out = service.get_container_client(settings.output_container)
    # Les correspondances sont persistées AVANT publication : un jeton publié est toujours réversible.
    runtime.mapping_store.save(result.records)
    out.upload_blob(names["pdf"], result.pdf, overwrite=True, content_settings=_ct("application/pdf"))
    out.upload_blob(names["text"], result.text.encode("utf-8"), overwrite=True, content_settings=_ct("text/plain; charset=utf-8"))
    out.upload_blob(names["report"], _json(result.report), overwrite=True, content_settings=_ct("application/json"))
    _trace(runtime, blob, "ok", result.report)
    if settings.delete_source_after_success:
        source.delete_blob()
    log.info("Document pseudonymisé doc=%s entites=%s", blob.doc_ref, result.report.get("entites_par_type"))
    return "ok"


def _quarantine(runtime: Runtime, blob: BlobRef, pdf_bytes: bytes, reasons: list[str], report: dict) -> str:
    settings = runtime.settings
    names = output_names(blob.name)
    q = runtime.blob_service.get_container_client(settings.quarantine_container)
    q.upload_blob(blob.name, pdf_bytes, overwrite=True, content_settings=_ct("application/pdf"))
    q.upload_blob(
        names["reason"],
        _json({"motifs": reasons, "rapport": report, "date": _now()}),
        overwrite=True,
        content_settings=_ct("application/json"),
    )
    _trace(runtime, blob, "quarantine", {**report, "motifs": reasons})
    if settings.delete_source_after_success:
        runtime.blob_service.get_blob_client(settings.input_container, blob.name).delete_blob()
    log.warning("Document en quarantaine doc=%s motifs=%s", blob.doc_ref, reasons)
    return "quarantine"


def _trace(runtime: Runtime, blob: BlobRef, status: str, report: dict) -> None:
    runtime.mapping_store.save_trace(
        {
            "id": str(uuid.uuid4()),
            "documentId": blob.doc_ref,
            "blob": f"{blob.container}/{blob.name}",
            "status": status,
            "processedAt": _now(),
            "report": report,
        }
    )


def _ct(content_type: str):
    from azure.storage.blob import ContentSettings

    return ContentSettings(content_type=content_type)


def _json(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
