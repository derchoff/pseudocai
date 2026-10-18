"""Pièce jointe d'un mail (file « pdf-jobs ») : pseudonymisée comme un document normal, mais le
résultat est écrit dans le dossier de travail du mail au lieu d'être publié.

Une pièce déjà terminée (message relivré) n'est pas retraitée ; la finalisation est retentée.
"""

from __future__ import annotations

import logging

from ..jobs import ORIGIN_IMAGE, Job
from ..outputs import delete_work_files, job_source_blob
from ..pipeline import PART, PipelineInput, run_pipeline
from .finalize import try_finalize
from .workspace import MailWorkspace

log = logging.getLogger(__name__)


def handle_mail_part(job: Job, runtime) -> str:
    """Traite une pièce jointe ; renvoie le statut du mail s'il vient d'être publié, sinon celui de la pièce."""
    settings = runtime.settings
    ws = MailWorkspace(runtime, job.parent["mail"])
    part = job.parent["part"]
    if ws.exists(f"parts/{part}.json"):
        delete_work_files(runtime, job)
        return try_finalize(runtime, ws) or "already_processed"

    if job.parent.get("status") == "quarantine":
        # Refusée par la préparation de l'image (floue, trop petite, illisible).
        marker = {"status": "quarantine", "reasons": job.parent.get("reasons", []), "report": job.parent.get("report", {})}
    else:
        original = job_source_blob(runtime, job).download_blob().readall()
        if job.origin == ORIGIN_IMAGE:
            from ..prepared_image import load_image_input

            work = runtime.blob_service.get_container_client(settings.work_container)
            pipeline_input = load_image_input(job, original, work, runtime.services.ocr, settings)
        else:
            pipeline_input = PipelineInput(pdf=original, source={"origine": "mail", "format": job.source_format})
        # Suite PART : pas de classement, le mail est classé en entier à sa finalisation.
        result = run_pipeline(pipeline_input, settings, runtime.services, steps=PART)
        if result.status == "ok":
            runtime.mapping_store.save(result.records)
            ws.put(f"out/part-{part}", result.output)  # nommée à la finalisation (out/names.json)
            # Texte publiable de la pièce (valeurs fictives ou jetons, selon le mode de sortie).
            ws.put(f"text/{part}.publie.txt", result.text or "")
        ws.put(f"text/{part}.txt", result.token_text or "")  # jetons : sert au classement du mail
        marker = {
            "status": result.status,
            "reasons": result.reasons,
            "report": result.report,
            "occurrences": result.occurrences,
            "image_dpi": job.prepared.preparation.get("dpi") if job.prepared else None,
        }
    ws.put_json(f"parts/{part}.json", marker)
    delete_work_files(runtime, job)
    log.info("Pièce de mail traitée doc=%s statut=%s", job.document_id, marker["status"])
    return try_finalize(runtime, ws) or marker["status"]
