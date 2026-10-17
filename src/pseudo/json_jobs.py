"""JSON de métadonnées de la GED : traitement (file « text-jobs ») et rattachement à son document.

Le JSON (<nom du document>.json) ne décide jamais de son dossier : il suit son document, dont le
classement dépend du contenu. L'ordre d'arrivée des deux fichiers est inconnu ; chacun ÉCRIT
d'abord, puis VÉRIFIE l'autre, de sorte qu'au moins l'un des deux voie l'autre :

    JSON  ──► pseudonymisé ──► work/json-metadata/<id du document>.json  (ou .motif.json si en doute)
                                     │ ensuite
                                     ▼
                         trace du document (Cosmos DB) ? ── non ──► attend dans work/
                                     │ oui
                                     ▼
                         copie dans le dossier du document ; chemin ajouté à sa trace

    Document ──► publication ou quarantaine, trace écrite ──► ensuite attach_json_metadata()
                                                               (copie si le JSON est déjà dans work/)

Une double copie est sans danger : même contenu, même nom. Le JSON reste dans work/ après copie
(purgé avec les dépôts, voir la règle « purge-json-metadata ») : un document retraité, ou classé
autrement, le recopie dans son nouveau dossier. Le chemin publié est ajouté à la trace du document :
le nettoyage des sorties d'un traitement précédent supprime donc aussi l'ancien JSON.

Où va le JSON :
- document publié, JSON pseudonymisé  -> à côté du document (facture.pdf.json), ou dans le dossier
  du mail (<nom du .eml>.json) ;
- document publié, JSON en doute     -> quarantine/, même dossier : JSON d'origine + motif ;
- document en quarantaine            -> à côté de l'original en quarantaine : JSON d'origine.
"""

from __future__ import annotations

import json
import logging
import posixpath

from .classification import Classification, TypeScore
from .jobs import ORIGIN_JSON, Job
from .json_metadata import JsonMetadataError, pseudonymise_json
from .json_rules import JsonRules
from .outputs import StorageContext, _json, job_source_blob, source_blob

log = logging.getLogger(__name__)

WORK_PREFIX = "json-metadata/"
JSON_SUFFIX = ".json"


def handle_json_job(job: Job, runtime) -> str:
    """Pseudonymise le JSON, l'écrit dans work/json-metadata/, PUIS tente de le rattacher."""
    settings = runtime.settings
    source = job_source_blob(runtime, job)
    if not source.exists():
        log.warning("JSON de métadonnées introuvable doc=%s", job.document_id)
        return "missing"
    catalog = runtime.services.fields() if runtime.services.fields else None
    rules = catalog.json_rules if catalog is not None else JsonRules()
    try:
        result = pseudonymise_json(source.download_blob().readall(), settings, runtime.services, rules)
        reasons, report = result.reasons, result.report
    except JsonMetadataError:
        result, reasons, report = None, ["json_illisible"], {}

    work = runtime.blob_service.get_container_client(settings.work_container)
    base = f"{WORK_PREFIX}{job.document_id}"
    if result is not None and result.status == "ok":
        if result.pipeline is not None:
            runtime.mapping_store.save(result.pipeline.records)  # avant publication : jeton réversible
        work.upload_blob(f"{base}.json", result.output, overwrite=True)
        work.upload_blob(f"{base}.occurrences.json", _json({"occurrences": result.occurrences}), overwrite=True)
        _delete(work, f"{base}.motif.json")
    else:
        work.upload_blob(f"{base}.motif.json", _json({"motifs": reasons, "rapport": report}), overwrite=True)
        _delete(work, f"{base}.json")
    log.info("JSON de métadonnées pseudonymisé doc=%s statut=%s", job.document_id, "ok" if result and result.status == "ok" else "quarantine")
    document_name = job.source_name[: -len(JSON_SUFFIX)]
    return attach_json_metadata(runtime, job.document_id, job.source_container, document_name) or "en_attente"


def attach_json_metadata(ctx: StorageContext, document_id: str, source_container: str, document_name: str) -> str | None:
    """Copie le JSON de métadonnées d'un document dans son dossier, si le document est terminé (trace
    écrite) et que le JSON est prêt dans work/. Renvoie le statut ou None s'il n'y a rien à faire.

    À appeler APRÈS l'écriture de la trace du document (le côté JSON l'appelle après avoir écrit
    dans work/) : au moins l'un des deux côtés voit l'autre.
    """
    settings = ctx.settings
    if not settings.json_metadata_enabled:
        return None
    work = ctx.blob_service.get_container_client(settings.work_container)
    base = f"{WORK_PREFIX}{document_id}"
    ready, doubtful = _exists(work, f"{base}.json"), _exists(work, f"{base}.motif.json")
    if not (ready or doubtful):
        return None
    try:
        trace = ctx.mapping_store.last_trace(document_id)
    except Exception:  # noqa: BLE001 - trace illisible : le document recopiera le JSON à son tour
        log.warning("Trace illisible doc=%s : JSON de métadonnées en attente", document_id)
        return None
    if not trace or trace.get("status") not in ("ok", "quarantine"):
        return None
    where = _destination(trace, document_name)
    if where is None:
        log.warning("Dossier du document introuvable dans sa trace doc=%s : JSON en attente", document_id)
        return None
    container, folder = where
    name = posixpath.basename(document_name) + JSON_SUFFIX
    path = posixpath.join(folder, name) if folder else name
    metadata = _classification(trace).metadata()

    if trace["status"] == "ok" and ready:
        files = [(settings.output_container, path, work.get_blob_client(f"{base}.json").download_blob().readall())]
        status = "ok"
    else:
        # Document en quarantaine, ou JSON en doute : JSON d'origine en quarantaine, à côté du document.
        original = source_blob(ctx, source_container, document_name + JSON_SUFFIX)
        if not original.exists():
            log.warning("JSON d'origine introuvable doc=%s", document_id)
            return None
        files = [(settings.quarantine_container, path, original.download_blob().readall())]
        if doubtful and trace["status"] == "ok":
            files.append((settings.quarantine_container, path.removesuffix(JSON_SUFFIX) + ".json.motif.json",
                          work.get_blob_client(f"{base}.motif.json").download_blob().readall()))
        status = "quarantine"
    written = []
    for target, blob_path, data in files:
        _upload(ctx, target, blob_path, data, metadata)
        written.append(f"{target}/{blob_path}")

    if status == "ok" and _exists(work, f"{base}.occurrences.json"):
        _save_occurrences(ctx, document_id, f"{settings.output_container}/{path}", work.get_blob_client(f"{base}.occurrences.json"))
    sorties = list(trace.get("sorties", []))
    if any(w not in sorties for w in written):
        ctx.mapping_store.save_trace({**trace, "sorties": sorties + [w for w in written if w not in sorties]})
    log.info("JSON de métadonnées rattaché doc=%s statut=%s", document_id, status)
    return status


def _destination(trace: dict, document_name: str) -> tuple[str, str] | None:
    """(container, dossier) du document dans sa trace : le fichier du document (publié ou original
    en quarantaine), sinon le dossier d'un mail (mail.json)."""
    base = posixpath.basename(document_name)
    sorties = [s.partition("/") for s in trace.get("sorties", [])]
    for container, _, path in sorties:
        if posixpath.basename(path) == base:
            return container, posixpath.dirname(path)
    for container, _, path in sorties:
        if posixpath.basename(path) == "mail.json":
            return container, posixpath.dirname(path)
    return None


def _classification(trace: dict) -> Classification:
    """Classification du document, relue depuis sa trace (métadonnées des blobs écrits)."""
    data = dict(trace.get("classification") or {})
    data["types"] = [TypeScore(**t) for t in data.get("types", [])]
    known = Classification.__dataclass_fields__
    return Classification(**{k: v for k, v in data.items() if k in known}) if data.get("principal") else Classification("", "", "")


def _save_occurrences(ctx: StorageContext, document_id: str, published: str, blob) -> None:
    """Occurrences du JSON (chemin du champ, sans valeur d'origine), sous l'identifiant <document>-json."""
    occurrences = json.loads(blob.download_blob().readall()).get("occurrences", [])
    items = [
        {"id": f"{document_id}-json-{n}", "documentId": f"{document_id}-json", "document": published,
         "origine": ORIGIN_JSON, **occ}
        for n, occ in enumerate(occurrences)
    ]
    ctx.mapping_store.save_occurrences(f"{document_id}-json", items)


def _upload(ctx: StorageContext, container: str, path: str, data: bytes, metadata: dict) -> None:
    """Écrit un fichier JSON avec les métadonnées de classification du document."""
    from azure.storage.blob import ContentSettings

    ctx.blob_service.get_container_client(container).upload_blob(
        path, data, overwrite=True, content_settings=ContentSettings(content_type="application/json"), metadata=metadata
    )


def _exists(container_client, name: str) -> bool:
    """Vrai si le blob existe."""
    return container_client.get_blob_client(name).exists()


def _delete(container_client, name: str) -> None:
    """Supprime un blob s'il existe."""
    from azure.core.exceptions import ResourceNotFoundError

    try:
        container_client.get_blob_client(name).delete_blob()
    except ResourceNotFoundError:
        pass
