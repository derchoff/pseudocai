"""Étape 3 — pseudonymisation d'un job : PDF d'origine, ou image préparée par l'étape 2.

Le document d'origine est relu dans incoming/ (il n'est jamais déplacé). Pour une image, les
fichiers préparés sont lus dans work/<job_id>/ et la sortie est produite au format d'origine.
"""

from __future__ import annotations

import logging

from .classification import STATUT_NON_APPLICABLE, unclassified
from .jobs import ORIGIN_IMAGE, Job
from .outputs import STAGE_PSEUDONYMISATION, delete_work_files, previous_trace, publish, quarantine
from .pipeline import PipelineInput, run_pipeline
from .runtime import Runtime

log = logging.getLogger(__name__)


def handle_pdf_job(body: bytes | str, runtime: Runtime) -> str:
    settings = runtime.settings
    job = Job.from_json(body)
    previous = previous_trace(runtime, job)
    if previous and previous.get("jobId") == job.job_id:
        # Message relivré par Service Bus après un traitement déjà terminé.
        log.info("Job déjà traité doc=%s job=%s", job.document_id, job.job_id)
        delete_work_files(runtime, job)
        return "already_processed"

    source = runtime.blob_service.get_blob_client(job.source_container, job.source_name)
    if not source.exists():
        log.warning("Document introuvable dans incoming/ doc=%s", job.document_id)
        delete_work_files(runtime, job)
        return "missing"
    original = source.download_blob().readall()

    if job.origin == ORIGIN_IMAGE:
        from .prepared_image import load_image_input

        work = runtime.blob_service.get_container_client(settings.work_container)
        pipeline_input = load_image_input(job, original, work, runtime.services.ocr, settings)
    else:
        pipeline_input = PipelineInput(pdf=original, source={"origine": job.origin, "format": job.source_format})

    log.info("Pseudonymisation doc=%s job=%s origine=%s", job.document_id, job.job_id, job.origin)
    result = run_pipeline(pipeline_input, settings, runtime.services)
    classification = result.classification or unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder)

    if result.status != "ok":
        quarantine(
            runtime, job, original, result.reasons, result.report, classification, previous, STAGE_PSEUDONYMISATION
        )
        return "quarantine"

    # Les correspondances sont persistées AVANT publication : un jeton publié est toujours réversible.
    runtime.mapping_store.save(result.records)
    image_dpi = job.prepared.preparation.get("dpi") if job.prepared else None
    publish(
        runtime,
        job,
        result.output,
        result.text,
        result.report,
        classification,
        previous,
        occurrences=result.occurrences,
        image_dpi=image_dpi,
    )
    log.info(
        "Document pseudonymisé doc=%s type=%s entites=%s",
        job.document_id,
        classification.principal,
        result.report.get("entites_par_type"),
    )
    return "ok"
