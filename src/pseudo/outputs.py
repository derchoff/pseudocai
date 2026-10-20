"""Écriture des résultats, commune aux trois étapes (routage, préparation image, pseudonymisation).

Arborescence produite : date et heure de DÉPOSE dans incoming/ (fuseau DEPOSIT_TIMEZONE), puis
dossier du type reconnu, puis chemin d'origine conservé :

    pseudonymised/year=AAAA/month=MM/day=JJ/hour=HH/domain=<dossier du type>/<chemin>.<ext d'origine>
                                                         | <chemin>.pseudonymise.txt | <chemin>.rapport.json
    quarantine/year=AAAA/month=MM/day=JJ/hour=HH/domain=<dossier du type>/<chemin>.<ext d'origine> (original)
                                                         | <chemin>.<ext>.motif.json

Les préfixes « year= », « month= »… rendent le chemin lisible et sont reconnus comme partitions
par les outils d'analyse (Spark, Synapse, Fabric). Une date de dépose différente (fichier redéposé)
donne un autre dossier : les sorties du traitement précédent sont alors supprimées, comme pour un
changement de type.

Chaque étape qui termine un document (succès ou quarantaine) :
- écrit ses fichiers avec les métadonnées de classification ;
- supprime les sorties d'un traitement précédent devenues obsolètes (changement de type,
  passage de pseudonymised/ à quarantine/ ou inversement) ;
- enregistre une trace (Cosmos DB) avec la liste des sorties écrites, et ses fiches de
  statistiques (statistics/) ;
- supprime les fichiers intermédiaires de work/ et, si demandé, le document d'entrée ;
- PUIS rattache son JSON de métadonnées de la GED s'il attend déjà dans work/json-metadata/
  (voir json_jobs.py : chaque côté écrit d'abord, vérifie ensuite).
"""

from __future__ import annotations

import json
import logging
import posixpath
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .classification import Classification
from .config import Settings
from .formats import CONTENT_TYPES
from .jobs import ORIGIN_JSON, ORIGIN_MAIL_TEXT, Job
from .mapping_store import MappingStore
from .statistics import record_statistics

log = logging.getLogger(__name__)

STAGE_ROUTING = "routage"
STAGE_IMAGE = "preparation_image"
STAGE_PSEUDONYMISATION = "pseudonymisation"


class StorageContext(Protocol):
    """Ce dont la publication a besoin : réglages, client Blob Storage et stockage des traces.

    source_blob_service : client du compte d'origine, s'il diffère du compte « data » (sinon None).
    """
    settings: Settings
    blob_service: object
    mapping_store: MappingStore
    source_blob_service: object | None


@dataclass
class SimpleStorageContext:
    """Implémentation simple de StorageContext (routeur, app images)."""
    settings: Settings
    blob_service: object
    mapping_store: MappingStore
    source_blob_service: object | None = None


def job_source_blob(ctx: StorageContext, job: Job):
    """Client du fichier à traiter : pièce jointe d'un mail dans work/ (compte « data »), sinon le
    document déposé dans le compte d'origine."""
    if job.source_container == ctx.settings.work_container:
        return ctx.blob_service.get_blob_client(job.source_container, job.source_name)
    return source_blob(ctx, job.source_container, job.source_name)


def source_blob(ctx: StorageContext, container: str, name: str):
    """Client du document déposé, dans le compte d'origine (le compte « data » par défaut)."""
    service = getattr(ctx, "source_blob_service", None) or ctx.blob_service
    return service.get_blob_client(container, name)


@dataclass
class OutputFile:
    """Fichier à écrire dans un container de sortie."""
    name: str  # chemin relatif au container
    data: bytes
    content_type: str


def deposit_folder(job: Job, dossier: str, settings: Settings) -> str:
    """Dossier de sortie d'un document : year=AAAA/month=MM/day=JJ/hour=HH/domain=<dossier du type>,
    d'après sa date de dépose dans incoming/ dans le fuseau DEPOSIT_TIMEZONE. Sans dossier de type
    (classification désactivée), le niveau domain= est omis."""
    local = job.deposited_local(settings.deposit_timezone)
    folder = f"year={local:%Y}/month={local:%m}/day={local:%d}/hour={local:%H}"
    return f"{folder}/domain={dossier}" if dossier else folder


def output_names(source_name: str, folder: str = "") -> dict[str, str]:
    """Noms des sorties d'un document : document, texte, rapport et motif de quarantaine."""
    prefix = f"{folder}/" if folder else ""
    stem, _ = posixpath.splitext(source_name)
    return {
        "document": f"{prefix}{source_name}",
        "text": f"{prefix}{stem}.pseudonymise.txt",
        "report": f"{prefix}{stem}.rapport.json",
        "reason": f"{prefix}{source_name}.motif.json",
    }


def previous_trace(ctx: StorageContext, job: Job) -> dict | None:
    """Dernière trace du document, ou None si elle est absente ou illisible."""
    try:
        return ctx.mapping_store.last_trace(job.document_id)
    except Exception:  # noqa: BLE001 - l'absence d'historique ne doit pas bloquer le traitement
        log.warning("Trace précédente illisible doc=%s : pas de nettoyage des anciennes sorties", job.document_id)
        return None


def publish(
    ctx: StorageContext,
    job: Job,
    document: bytes,
    text: str,
    report: dict,
    classification: Classification,
    previous: dict | None,
    occurrences: list[dict] | None = None,
    image_dpi: int | None = None,
) -> list[str]:
    """Écrit les sorties pseudonymisées (document au format d'origine, texte, rapport) et les
    occurrences (zones remplacées) qui permettent la ré-identification par document et position."""
    settings = ctx.settings
    names = output_names(job.source_name, deposit_folder(job, classification.dossier, settings))
    files = [
        OutputFile(names["document"], document, CONTENT_TYPES[job.source_format]),
        OutputFile(names["text"], text.encode("utf-8"), "text/plain; charset=utf-8"),
        OutputFile(names["report"], _json(report), "application/json"),
    ]
    outputs = _write(ctx, settings.output_container, files, classification)
    ctx.mapping_store.save_occurrences(
        job.document_id, _occurrence_items(job, outputs[0], occurrences or [], image_dpi)
    )
    _finish(ctx, job, "ok", report, classification, outputs, previous, STAGE_PSEUDONYMISATION)
    attach_metadata_json(ctx, job)
    return outputs


def _occurrence_items(job: Job, document_path: str, occurrences: list[dict], image_dpi: int | None) -> list[dict]:
    """Fiches Cosmos DB « occurrences » : aucune valeur d'origine (elle reste chiffrée dans
    « mappings », retrouvée par le jeton). Pour une image, la position est aussi donnée en pixels."""
    items = []
    for n, occ in enumerate(occurrences):
        item = {
            "id": f"{job.job_id}-{n}",
            "documentId": job.document_id,
            "jobId": job.job_id,
            "document": document_path,
            "source": f"{job.source_container}/{job.source_name}",
            "origine": job.origin,
            **occ,
            "unite_position": "point",
        }
        if image_dpi:
            item["position_pixels"] = [round(v * image_dpi / 72) for v in occ["position"]]
        items.append(item)
    return items


def quarantine(
    ctx: StorageContext,
    job: Job,
    original: bytes,
    reasons: list[str],
    report: dict,
    classification: Classification,
    previous: dict | None,
    stage: str,
) -> list[str]:
    """Copie l'original en quarantaine avec le motif (le document d'entrée reste dans incoming/)."""
    settings = ctx.settings
    names = output_names(job.source_name, deposit_folder(job, classification.dossier, settings))
    motif = {
        "motifs": reasons,
        "etape": stage,
        "origine": job.origin,
        "format": job.source_format,
        "classification": classification.to_dict(),
        "rapport": report,
        "date": _now(),
    }
    files = [
        OutputFile(names["document"], original, CONTENT_TYPES.get(job.source_format, "application/octet-stream")),
        OutputFile(names["reason"], _json(motif), "application/json"),
    ]
    outputs = _write(ctx, settings.quarantine_container, files, classification)
    _finish(ctx, job, "quarantine", {**report, "motifs": reasons}, classification, outputs, previous, stage)
    attach_metadata_json(ctx, job)
    log.warning(
        "Document en quarantaine doc=%s etape=%s type=%s motifs=%s",
        job.document_id,
        stage,
        classification.principal,
        reasons,
    )
    return outputs


def attach_metadata_json(ctx: StorageContext, job: Job) -> None:
    """Rattache le JSON de métadonnées d'un document seul (PDF, image, ou fichier refusé au
    routage), à appeler APRÈS l'écriture de sa trace. Rien pour un JSON lui-même, ni pour une partie
    de mail (le mail entier le fait à sa finalisation)."""
    if job.parent is not None or job.origin in (ORIGIN_JSON, ORIGIN_MAIL_TEXT):
        return
    from .json_jobs import attach_json_metadata

    attach_json_metadata(ctx, job.document_id, job.source_container, job.source_name)


def delete_work_files(ctx: StorageContext, job: Job) -> None:
    """Supprime les fichiers intermédiaires (copies du document : données personnelles)."""
    if job.prepared is None:
        return
    for path in job.prepared.paths():
        _delete_if_exists(ctx, ctx.settings.work_container, path)


def _write(ctx: StorageContext, container: str, files: list[OutputFile], classification: Classification) -> list[str]:
    """Écrit les fichiers dans le container, avec la classification en métadonnées. Renvoie leurs chemins."""
    from azure.storage.blob import ContentSettings

    client = ctx.blob_service.get_container_client(container)
    metadata = classification.metadata()
    for f in files:
        client.upload_blob(
            f.name,
            f.data,
            overwrite=True,
            content_settings=ContentSettings(content_type=f.content_type),
            metadata=metadata,
        )
    return [f"{container}/{f.name}" for f in files]


def _finish(
    ctx: StorageContext,
    job: Job,
    status: str,
    report: dict,
    classification: Classification,
    outputs: list[str],
    previous: dict | None,
    stage: str,
) -> None:
    """Termine un traitement : supprime les anciennes sorties, écrit la trace et nettoie work/.

    Supprime aussi la source si DELETE_SOURCE_AFTER_SUCCESS est activé.
    """
    # Trace précédente relue maintenant : pendant le traitement (lu au début), un JSON de métadonnées
    # a pu y ajouter sa copie ; elle doit partir avec les autres sorties devenues obsolètes.
    latest = previous_trace(ctx, job)
    if latest is not None and latest.get("jobId") != job.job_id:
        previous = latest
    _remove_previous_outputs(ctx, previous, outputs)
    ctx.mapping_store.save_trace(
        {
            "id": str(uuid.uuid4()),
            "documentId": job.document_id,
            "jobId": job.job_id,
            "blob": f"{job.source_container}/{job.source_name}",
            "origin": job.origin,
            "sourceFormat": job.source_format,
            "stage": stage,
            "status": status,
            "processedAt": _now(),
            # Dépôt traité : permet au JSON de métadonnées de reconnaître une trace périmée (le
            # document a été redéposé depuis, et son nouveau traitement n'est pas fini).
            "depositedAt": job.deposited_at or job.received_at,
            "classification": classification.to_dict(),
            "preparation": job.prepared.preparation if job.prepared else None,
            "sorties": outputs,
            "report": report,
        }
    )
    record_statistics(ctx, job, status, report, classification)
    delete_work_files(ctx, job)
    if ctx.settings.delete_source_after_success:
        _delete_blob(source_blob(ctx, job.source_container, job.source_name))


def _remove_previous_outputs(ctx: StorageContext, previous: dict | None, current_outputs: list[str]) -> None:
    """Supprime les sorties d'un traitement précédent qui ne sont plus produites (ex. changement de type).

    Ne supprime jamais hors des containers pseudonymised/ et quarantine/.
    """
    if not previous:
        return
    settings = ctx.settings
    allowed = {settings.output_container, settings.quarantine_container}
    removed = 0
    for path in previous.get("sorties", []):
        if path in current_outputs:
            continue
        container, _, name = path.partition("/")
        if container not in allowed or not name:
            continue  # ne jamais supprimer ailleurs que dans les containers de sortie
        removed += _delete_if_exists(ctx, container, name)
    if removed:
        log.info(
            "%d sortie(s) d'un traitement précédent supprimée(s) (type précédent : %s)",
            removed,
            (previous.get("classification") or {}).get("principal"),
        )


def _delete_if_exists(ctx: StorageContext, container: str, name: str) -> int:
    """Supprime un blob du compte « data » s'il existe ; renvoie 1 s'il a été supprimé, 0 sinon."""
    return _delete_blob(ctx.blob_service.get_blob_client(container, name))


def _delete_blob(blob) -> int:
    """Supprime le blob s'il existe ; renvoie 1 s'il a été supprimé, 0 sinon."""
    from azure.core.exceptions import ResourceNotFoundError

    try:
        blob.delete_blob()
        return 1
    except ResourceNotFoundError:
        return 0


def _json(data: dict) -> bytes:
    """Sérialise en JSON lisible (UTF-8, indenté)."""
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def _now() -> str:
    """Date et heure UTC au format ISO 8601."""
    return datetime.now(timezone.utc).isoformat()
