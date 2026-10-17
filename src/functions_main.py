"""Fonctions de l'app principale : étape ① (routage) et étape ③ (pseudonymisation).

    incoming-events ──► route_incoming ──► pdf-jobs   ──► pseudonymise_job
                                       ├─► image-jobs ──► (app images : functions_images.py)
                                       ├─► mail-jobs  ──► split_mail ──► text-jobs (texte du mail)
                                       │                                 pdf-jobs / image-jobs (pièces)
                                       └─► text-jobs  ──► pseudonymise_text
                                                          (texte d'un mail, JSON <document>.json)

Chaque déclencheur lit une file Service Bus. Une exception remet le message dans sa file
(nouvel essai), puis en dead-letter après max_delivery_count tentatives.
"""

import logging
import os
import threading

import azure.functions as func

from pseudo.config import Settings
from pseudo.handler import handle_pdf_job
from pseudo.text_jobs import handle_text_job
from pseudo.outputs import SimpleStorageContext
from pseudo.mails.split import handle_mail_job
from pseudo.queues import QueueSender
from pseudo.router import QUEUE_IMAGE, QUEUE_MAIL, QUEUE_PDF, QUEUE_TEXT, route_event
from pseudo.runtime import Runtime, build_runtime, build_storage_context, credential

bp = func.Blueprint()

_lock = threading.Lock()
_runtime: Runtime | None = None
_storage: SimpleStorageContext | None = None
_sender: QueueSender | None = None


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


def get_sender() -> QueueSender:
    """Envoi de plusieurs messages Service Bus (tâches des pièces jointes d'un mail)."""
    global _sender
    with _lock:
        if _sender is None:
            _sender = QueueSender.from_env(credential())
        return _sender


@bp.function_name("route_incoming")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%INCOMING_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="pdfjobs", queue_name="%PDF_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="imagejobs", queue_name="%IMAGE_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="mailjobs", queue_name="%MAIL_QUEUE_NAME%", connection="ServiceBusConnection")
@bp.service_bus_queue_output(arg_name="textjobs", queue_name="%TEXT_QUEUE_NAME%", connection="ServiceBusConnection")
def route_incoming(
    msg: func.ServiceBusMessage,
    pdfjobs: func.Out[str],
    imagejobs: func.Out[str],
    mailjobs: func.Out[str],
    textjobs: func.Out[str],
) -> None:
    """Function Service Bus : route un dépôt dans incoming/ vers la file PDF, image, mail ou texte,
    ou en quarantaine."""
    result = route_event(msg.get_body(), get_storage())
    if result.queue == QUEUE_PDF:
        pdfjobs.set(result.job.to_json())
    elif result.queue == QUEUE_IMAGE:
        imagejobs.set(result.job.to_json())
    elif result.queue == QUEUE_MAIL:
        mailjobs.set(result.job.to_json())
    elif result.queue == QUEUE_TEXT:
        textjobs.set(result.job.to_json())
    logging.info("Événement %s routé : %s", msg.message_id, result.status)


@bp.function_name("pseudonymise_job")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%PDF_QUEUE_NAME%", connection="ServiceBusConnection")
def pseudonymise_job(msg: func.ServiceBusMessage) -> None:
    """Function Service Bus : pseudonymise le document d'un job de la file pdf-jobs."""
    logging.info("Job %s (tentative %s)", msg.message_id, msg.delivery_count)
    status = handle_pdf_job(msg.get_body(), get_runtime())
    logging.info("Job %s traité : %s", msg.message_id, status)


@bp.function_name("split_mail")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%MAIL_QUEUE_NAME%", connection="ServiceBusConnection")
def split_mail(msg: func.ServiceBusMessage) -> None:
    """Function Service Bus : découpe un mail (sans agent IA) et envoie ses tâches : le texte du mail
    dans text-jobs, une tâche par pièce jointe dans pdf-jobs ou image-jobs.

    Plusieurs messages à envoyer : SDK Service Bus (une liaison de sortie n'en accepte qu'un).
    """
    logging.info("Mail %s (tentative %s)", msg.message_id, msg.delivery_count)
    children = handle_mail_job(msg.get_body(), get_runtime())
    pdf = [body for queue, body in children if queue == QUEUE_PDF]
    images = [body for queue, body in children if queue == QUEUE_IMAGE]
    texts = [body for queue, body in children if queue == QUEUE_TEXT]
    sender = get_sender()
    sender.send(os.environ["TEXT_QUEUE_NAME"], texts)
    sender.send(os.environ["PDF_QUEUE_NAME"], pdf)
    sender.send(os.environ["IMAGE_QUEUE_NAME"], images)
    logging.info("Mail %s découpé : %d pièce(s) PDF, %d image(s)", msg.message_id, len(pdf), len(images))


@bp.function_name("pseudonymise_text")
@bp.service_bus_queue_trigger(arg_name="msg", queue_name="%TEXT_QUEUE_NAME%", connection="ServiceBusConnection")
def pseudonymise_text(msg: func.ServiceBusMessage) -> None:
    """Function Service Bus : pseudonymise un texte de la file text-jobs (JSON de métadonnées) et le
    rattache à son document s'il est déjà traité."""
    logging.info("Texte %s (tentative %s)", msg.message_id, msg.delivery_count)
    status = handle_text_job(msg.get_body(), get_runtime())
    logging.info("Texte %s traité : %s", msg.message_id, status)
