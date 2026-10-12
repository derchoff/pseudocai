"""Lecture des événements Event Grid (BlobCreated) livrés par Service Bus."""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class BlobEvent:
    """Fichier visé par un événement BlobCreated : URL, container et chemin."""
    url: str
    container: str
    name: str


def parse_blob_event(body: bytes | str) -> BlobEvent:
    """Extrait le fichier visé d'un événement Event Grid (objet ou tableau JSON)."""
    data = json.loads(body)
    if isinstance(data, list):  # Event Grid peut livrer un tableau d'événements
        data = data[0]
    url = data["data"]["url"]
    path = unquote(urlparse(url).path).lstrip("/")
    container, _, name = path.partition("/")
    return BlobEvent(url=url, container=container, name=name)
