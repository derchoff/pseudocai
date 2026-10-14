"""Dossier de travail d'un mail dans work/mails/<job du mail>/ (compte « data », purgé en fin de traitement).

    state.json      état du mail : job, nom du dossier, pièces attendues, résultat du corps
    in/<n><ext>     pièces jointes d'origine (lues par les tâches des pièces)
    out/…           mail.json, body.html|txt et pièces pseudonymisées (noms définitifs)
    text/…          textes pseudonymisés avec les jetons (classement de l'ensemble)
    parts/<n>.json  une pièce terminée : statut, motifs, rapport, occurrences
    finalize.lock   pris par la seule tâche qui publie le mail (création exclusive)

Ces fichiers contiennent des données personnelles (pièces d'origine) ou leurs versions
pseudonymisées : ils sont supprimés par la finalisation, et la règle purge-work les purge sinon.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ..outputs import StorageContext


class MailWorkspace:
    """Accès aux fichiers de travail d'un mail."""

    def __init__(self, ctx: StorageContext, prefix: str):
        """prefix : « mails/<job du mail>/ »."""
        self.prefix = prefix
        self._container = ctx.blob_service.get_container_client(ctx.settings.work_container)

    @classmethod
    def for_job(cls, ctx: StorageContext, job_id: str) -> "MailWorkspace":
        """Dossier de travail du mail traité par le job job_id."""
        return cls(ctx, f"mails/{job_id}/")

    def put(self, path: str, data: bytes | str) -> str:
        """Écrit un fichier (remplacé s'il existe) ; renvoie son chemin dans work/."""
        name = self.prefix + path
        self._container.upload_blob(name, data.encode("utf-8") if isinstance(data, str) else data, overwrite=True)
        return name

    def put_json(self, path: str, data: dict) -> str:
        """Écrit un fichier JSON."""
        return self.put(path, json.dumps(data, ensure_ascii=False))

    def get(self, path: str) -> bytes:
        """Contenu d'un fichier."""
        return self._container.get_blob_client(self.prefix + path).download_blob().readall()

    def get_json(self, path: str) -> dict:
        """Contenu d'un fichier JSON."""
        return json.loads(self.get(path))

    def exists(self, path: str) -> bool:
        """Vrai si le fichier existe."""
        return self._container.get_blob_client(self.prefix + path).exists()

    def acquire_lock(self) -> bool:
        """Prend le verrou de publication ; False s'il est déjà pris (une autre tâche publie)."""
        from azure.core.exceptions import ResourceExistsError

        try:
            self._container.upload_blob(
                self.prefix + "finalize.lock", datetime.now(timezone.utc).isoformat().encode(), overwrite=False
            )
            return True
        except ResourceExistsError:
            return False

    def release_lock(self) -> None:
        """Rend le verrou (après une erreur de publication : le prochain essai pourra publier)."""
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self._container.get_blob_client(self.prefix + "finalize.lock").delete_blob()
        except ResourceNotFoundError:
            pass

    def delete_all(self) -> None:
        """Supprime tout le dossier de travail du mail."""
        from azure.core.exceptions import ResourceNotFoundError

        for blob in list(self._container.list_blobs(name_starts_with=self.prefix)):
            try:
                self._container.get_blob_client(blob.name).delete_blob()
            except ResourceNotFoundError:
                pass
