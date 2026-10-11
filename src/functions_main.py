"""Fonctions de l'app principale : étape ① (routage) et étape ③ (pseudonymisation).

    incoming-events ──► route_incoming ──► pdf-jobs   ──► pseudonymise_job
                                       └─► image-jobs ──► (app images : functions_images.py)

Chaque déclencheur lit une file Service Bus. Une exception remet le message dans sa file
(nouvel essai), puis en dead-letter après max_delivery_count tentatives.
"""

import logging
import threading

import azure.functions as func

from pseudo.config import Settings
from pseudo.handler import handle_pdf_job
from pseudo.outputs import SimpleStorageContext
from pseudo.router import QUEUE_IMAGE, QUEUE_PDF, route_event
from pseudo.runtime import Runtime, build_runtime, build_storage_context

bp = func.Blueprint()

_lock = threading.Lock()
_runtime: Runtime | None = None
_storage: SimpleStorageContext | None = None


def get_runtime() -> Runtime:
    """Clients Azure, prompts et clés chargés une fois par instance (étape de pseudonymisation)."""
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = build_runtime(Settings.from_env())
        return _runtime


def get_storage() -> SimpleStorageContext:
    """Contexte léger du routeur : stockage et traces uniquement."""
    global _storage
    with _lock:
        if _storage is None:
            _storage = build_storage_context(Settings.from_env())
        return _storage


@bp.function_name("route_incoming")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%INCOMING_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="pdfjobs", queue_name="%PDF_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="imagejobs", queue_name="%IMAGE_QUEUE_NAME%", connection="ServiceBusConnection")
def route_incoming(msg: func.ServiceBusMessage, pdfjobs: func.Out[str], imagejobs: func.Out[str]) -> None:
    result = route_event(msg.get_body(), get_storage())
    if result.queue == QUEUE_PDF:
        pdfjobs.set(result.job.to_json())
    elif result.queue == QUEUE_IMAGE:
        imagejobs.set(result.job.to_json())
    logging.info("Événement %s routé : %s", msg.message_id, result.status)


@bp.function_name("pseudonymise_job")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%PDF_QUEUE_NAME%", connection="ServiceBusConnection")
def pseudonymise_job(msg: func.ServiceBusMessage) -> None:
    logging.info("Job %s (tentative %s)", msg.message_id, msg.delivery_count)
    status = handle_pdf_job(msg.get_body(), get_runtime())
    logging.info("Job %s traité : %s", msg.message_id, status)
