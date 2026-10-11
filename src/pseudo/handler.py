"""Traitement d'un événement BlobCreated : lecture, pipeline, écriture des sorties classées par type.

Arborescence produite (le chemin d'origine est conservé sous le dossier du type) :

    pseudonymised/<dossier du type>/<chemin>.pdf | .pseudonymise.txt | .rapport.json
    quarantine/<dossier du type>/<chemin>.pdf (original) | .pdf.motif.json

Le dossier vaut « non-classe » (UNCLASSIFIED_FOLDER) si le type n'a pas pu être déterminé, et
est absent si la classification est désactivée.
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

from .classification import STATUT_NON_APPLICABLE, Classification, unclassified
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
        """Identifiant pseudonyme et stable du document (logs, partition Cosmos DB).

        Le nom de fichier peut contenir un nom de personne : il n'apparaît jamais dans les logs."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]


def parse_event(body: bytes | str) -> BlobRef:
    data = json.loads(body)
    if isinstance(data, list):  # Event Grid peut livrer un tableau d'événements
        data = data[0]
    url = data["data"]["url"]
    path = unquote(urlparse(url).path).lstrip("/")
    container, _, name = path.partition("/")
    return BlobRef(url=url, container=container, name=name)


def output_names(blob_name: str, folder: str = "") -> dict[str, str]:
    prefix = f"{folder}/" if folder else ""
    stem, _ = posixpath.splitext(blob_name)
    return {
        "pdf": f"{prefix}{blob_name}",
        "text": f"{prefix}{stem}.pseudonymise.txt",
        "report": f"{prefix}{stem}.rapport.json",
        "reason": f"{prefix}{blob_name}.motif.json",
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
    previous = _previous_trace(runtime, blob)
    props = source.get_blob_properties()
    if props.size > settings.max_file_mb * 1024 * 1024:
        classification = unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder)
        return _quarantine(
            runtime, blob, source.download_blob().readall(), ["fichier_trop_volumineux"], {}, classification, previous
        )

    pdf_bytes = source.download_blob().readall()
    log.info("Traitement doc=%s taille=%d", blob.doc_ref, len(pdf_bytes))
    result = run_pipeline(pdf_bytes, settings, runtime.services)
    classification = result.classification or unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder)

    if result.status != "ok":
        return _quarantine(runtime, blob, pdf_bytes, result.reasons, result.report, classification, previous)

    names = output_names(blob.name, classification.dossier)
    out = service.get_container_client(settings.output_container)
    metadata = classification.metadata()
    # Les correspondances sont persistées AVANT publication : un jeton publié est toujours réversible.
    runtime.mapping_store.save(result.records)
    out.upload_blob(names["pdf"], result.pdf, overwrite=True, content_settings=_ct("application/pdf"), metadata=metadata)
    out.upload_blob(
        names["text"],
        result.text.encode("utf-8"),
        overwrite=True,
        content_settings=_ct("text/plain; charset=utf-8"),
        metadata=metadata,
    )
    out.upload_blob(
        names["report"], _json(result.report), overwrite=True, content_settings=_ct("application/json"), metadata=metadata
    )
    outputs = [f"{settings.output_container}/{names[k]}" for k in ("pdf", "text", "report")]
    _remove_previous_outputs(runtime, previous, outputs)
    _trace(runtime, blob, "ok", result.report, classification, outputs)
    if settings.delete_source_after_success:
        source.delete_blob()
    log.info(
        "Document pseudonymisé doc=%s type=%s entites=%s",
        blob.doc_ref,
        classification.principal,
        result.report.get("entites_par_type"),
    )
    return "ok"


def _quarantine(
    runtime: Runtime,
    blob: BlobRef,
    pdf_bytes: bytes,
    reasons: list[str],
    report: dict,
    classification: Classification,
    previous: dict | None,
) -> str:
    settings = runtime.settings
    names = output_names(blob.name, classification.dossier)
    metadata = classification.metadata()
    q = runtime.blob_service.get_container_client(settings.quarantine_container)
    q.upload_blob(names["pdf"], pdf_bytes, overwrite=True, content_settings=_ct("application/pdf"), metadata=metadata)
    q.upload_blob(
        names["reason"],
        _json({"motifs": reasons, "classification": classification.to_dict(), "rapport": report, "date": _now()}),
        overwrite=True,
        content_settings=_ct("application/json"),
        metadata=metadata,
    )
    outputs = [f"{settings.quarantine_container}/{names[k]}" for k in ("pdf", "reason")]
    _remove_previous_outputs(runtime, previous, outputs)
    _trace(runtime, blob, "quarantine", {**report, "motifs": reasons}, classification, outputs)
    if settings.delete_source_after_success:
        runtime.blob_service.get_blob_client(settings.input_container, blob.name).delete_blob()
    log.warning("Document en quarantaine doc=%s type=%s motifs=%s", blob.doc_ref, classification.principal, reasons)
    return "quarantine"


def _previous_trace(runtime: Runtime, blob: BlobRef) -> dict | None:
    try:
        return runtime.mapping_store.last_trace(blob.doc_ref)
    except Exception:  # noqa: BLE001 - l'absence d'historique ne doit pas bloquer le traitement
        log.warning("Trace précédente illisible doc=%s : pas de nettoyage des anciennes sorties", blob.doc_ref)
        return None


def _remove_previous_outputs(runtime: Runtime, previous: dict | None, current_outputs: list[str]) -> None:
    """Retraitement : supprime les sorties du passage précédent qui ne sont plus produites
    (changement de type, ou passage de pseudonymised/ à quarantine/ et inversement)."""
    if not previous:
        return
    from azure.core.exceptions import ResourceNotFoundError

    settings = runtime.settings
    allowed = {settings.output_container, settings.quarantine_container}
    removed = 0
    for path in previous.get("sorties", []):
        if path in current_outputs:
            continue
        container, _, name = path.partition("/")
        if container not in allowed or not name:
            continue  # ne jamais supprimer ailleurs que dans les containers de sortie
        try:
            runtime.blob_service.get_blob_client(container, name).delete_blob()
            removed += 1
        except ResourceNotFoundError:
            pass  # déjà supprimé
    if removed:
        log.info(
            "%d sortie(s) d'un traitement précédent supprimée(s) (type précédent : %s)",
            removed,
            (previous.get("classification") or {}).get("principal"),
        )


def _trace(
    runtime: Runtime,
    blob: BlobRef,
    status: str,
    report: dict,
    classification: Classification,
    outputs: list[str],
) -> None:
    runtime.mapping_store.save_trace(
        {
            "id": str(uuid.uuid4()),
            "documentId": blob.doc_ref,
            "blob": f"{blob.container}/{blob.name}",
            "status": status,
            "processedAt": _now(),
            "classification": classification.to_dict(),
            "sorties": outputs,
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
