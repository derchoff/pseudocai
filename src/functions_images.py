"""Fonction de l'app images : étape ② (préparation des images).

    image-jobs ──► prepare_image ──► work/<job_id>/ (render.pdf, ocr.pdf, transforms.json)
                                 └─► pdf-jobs (job enrichi), ou quarantine/non-classe/ si l'image
                                     est illisible, trop grande, trop petite ou floue

Seul module qui importe le package pseudo.images (OpenCV) : il n'est chargé que dans l'app
images (FUNCTION_APP_ROLE=images), qui reçoit en plus les dépendances de requirements-images.txt.
"""

import logging
import threading

import azure.functions as func

from pseudo.config import Settings
from pseudo.images.prepare import handle_image_job
from pseudo.outputs import SimpleStorageContext
from pseudo.runtime import build_storage_context

bp = func.Blueprint()

_lock = threading.Lock()
_storage: SimpleStorageContext | None = None


def get_storage() -> SimpleStorageContext:
    global _storage
    with _lock:
        if _storage is None:
            _storage = build_storage_context(Settings.from_env())
        return _storage


@bp.function_name("prepare_image")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%IMAGE_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="pdfjobs", queue_name="%PDF_QUEUE_NAME%", connection="ServiceBusConnection")
def prepare_image(msg: func.ServiceBusMessage, pdfjobs: func.Out[str]) -> None:
    logging.info("Image %s (tentative %s)", msg.message_id, msg.delivery_count)
    job = handle_image_job(msg.get_body(), get_storage())
    if job is not None:
        pdfjobs.set(job.to_json())
    logging.info("Image %s : %s", msg.message_id, "préparée" if job else "non transmise")
