"""Point d'entrée Azure Functions : un message Service Bus = un événement BlobCreated Event Grid."""

import logging
import threading

import azure.functions as func

from pseudo.config import Settings
from pseudo.handler import handle_event
from pseudo.runtime import Runtime, build_runtime

app = func.FunctionApp()

_runtime: Runtime | None = None
_lock = threading.Lock()


def get_runtime() -> Runtime:
    """Clients Azure et clés chargés une fois par instance."""
    global _runtime
    with _lock:
        if _runtime is None:
            _runtime = build_runtime(Settings.from_env())
        return _runtime


@app.function_name("pseudonymise_pdf")
@app.service_bus_queue_trigger(
    arg_name="msg",
    queue_name="%SERVICEBUS_QUEUE_NAME%",
    connection="ServiceBusConnection",
)
def pseudonymise_pdf(msg: func.ServiceBusMessage) -> None:
    logging.info("Message %s (tentative %s)", msg.message_id, msg.delivery_count)
    # Une exception remet le message dans la file (retry) puis en dead-letter après
    # max_delivery_count tentatives.
    status = handle_event(msg.get_body(), get_runtime())
    logging.info("Message %s traité : %s", msg.message_id, status)
