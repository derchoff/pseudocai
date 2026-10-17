"""File « text-jobs » : les textes à pseudonymiser, aiguillés selon leur origine.

    json-metadata  JSON de métadonnées de la GED (<nom du document>.json) -> json_jobs.py
    mail-text      texte d'un mail : en-têtes, objet, noms des pièces, corps -> mails/text.py
"""

from __future__ import annotations

from .jobs import ORIGIN_JSON, ORIGIN_MAIL_TEXT, Job


def handle_text_job(body: bytes | str, runtime) -> str:
    """Traite une tâche de la file « text-jobs » ; renvoie son statut."""
    job = Job.from_json(body)
    if job.origin == ORIGIN_JSON:
        from .json_jobs import handle_json_job

        return handle_json_job(job, runtime)
    if job.origin == ORIGIN_MAIL_TEXT:
        from .mails.text import handle_mail_text

        return handle_mail_text(job, runtime)
    raise ValueError(f"Origine inattendue dans text-jobs : {job.origin}")
