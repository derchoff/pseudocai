"""Envoi de plusieurs messages dans une file Service Bus, avec le SDK.

Une liaison de sortie Azure Functions vers Service Bus n'accepte qu'UN message (str ou bytes) :
func.Out[list[str]] est refusé au chargement et empêche alors TOUTES les Functions de l'app de
démarrer. Le découpage d'un mail, qui crée une tâche par pièce jointe, passe donc par le SDK,
avec la même identité managée (rôle Azure Service Bus Data Sender sur les files visées).
"""

from __future__ import annotations

import os


class QueueSender:
    """Envoie des lots de messages dans les files d'un espace de noms Service Bus."""

    def __init__(self, namespace: str, credential):
        """namespace : nom complet (<nom>.servicebus.windows.net) ; credential : identité Azure."""
        from azure.servicebus import ServiceBusClient

        self._client = ServiceBusClient(fully_qualified_namespace=namespace, credential=credential)

    @classmethod
    def from_env(cls, credential) -> "QueueSender":
        """Espace de noms lu dans le réglage de la connexion des déclencheurs (ServiceBusConnection)."""
        return cls(os.environ["ServiceBusConnection__fullyQualifiedNamespace"], credential)

    def send(self, queue: str, bodies: list[str]) -> None:
        """Envoie les messages dans la file, par lots (un nouveau lot quand le précédent est plein)."""
        if not bodies:
            return
        from azure.servicebus import ServiceBusMessage

        with self._client.get_queue_sender(queue) as sender:
            batch = sender.create_message_batch()
            for body in bodies:
                try:
                    batch.add_message(ServiceBusMessage(body))
                except ValueError:  # lot plein
                    sender.send_messages(batch)
                    batch = sender.create_message_batch()
                    batch.add_message(ServiceBusMessage(body))
            sender.send_messages(batch)
