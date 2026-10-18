"""Texte d'un mail (file « text-jobs ») : en-têtes, objet, noms des pièces jointes et corps,
pseudonymisés en UN seul passage dans le pipeline (agents IA compris).

Le découpage (split.py) n'appelle pas les agents IA : il envoie cette tâche et une tâche par pièce
jointe. Les garder ensemble ici donne le contexte à l'agent (« De : Jean DUPONT » éclaire la
signature « Jean » du corps) et la propagation retrouve un nom partout ; une même valeur reçoit de
toute façon la même valeur fictive d'une tâche à l'autre (tirage déterminé par la clé).

Écrit dans le dossier de travail du mail :
    out/mail.json · out/body.html|txt · out/body.pseudonymise.txt
    out/names.json      nom final (pseudonymisé) de chaque pièce, utilisé à la finalisation
    text/body.txt       texte à jetons (classement de l'ensemble)
    parts/texte.json    marqueur : statut, motifs, rapport, occurrences
"""

from __future__ import annotations

import logging

from ..jobs import Job
from ..outputs import job_source_blob
from ..pipeline import TEXT, PipelineInput, run_pipeline
from .document import build_mail_text, read_back
from .finalize import TEXT_PART, try_finalize
from .html_body import HtmlBody
from .parse import parse_eml
from .workspace import BODY_TEXT_FILE, MailWorkspace

log = logging.getLogger(__name__)


def handle_mail_text(job: Job, runtime) -> str:
    """Pseudonymise le texte du mail ; renvoie le statut du mail s'il vient d'être publié, sinon
    celui du texte."""
    settings = runtime.settings
    ws = MailWorkspace(runtime, job.parent["mail"])
    if ws.exists(f"parts/{TEXT_PART}.json"):  # message relivré : déjà traité
        return try_finalize(runtime, ws) or "already_processed"
    state = ws.get_json("state.json")
    mail = parse_eml(job_source_blob(runtime, job).download_blob().readall())
    kept = {p["index"] for p in state["parts"]}
    mail.attachments = [a for a in mail.attachments if a.index in kept]  # mêmes pièces qu'au découpage

    html = HtmlBody(mail.body) if mail.body_format == "html" else None
    built = build_mail_text(mail, html)
    result = run_pipeline(
        PipelineInput(pdf=b"", content=built.content, source={"origine": "mail", "format": "eml"}),
        settings,
        runtime.services,
        steps=TEXT,
    )
    runtime.mapping_store.save(result.records)  # avant toute publication : un jeton publié est réversible
    pseudo = read_back(built, mail, result.spans, html)

    body_file = "body.html" if html is not None else "body.txt"
    ws.put("text/body.txt", result.token_text or "")
    ws.put(f"out/{body_file}", pseudo.body)
    ws.put(f"out/{BODY_TEXT_FILE}", pseudo.body_text)
    ws.put_json("out/mail.json", _mail_json(mail, state["folder"], pseudo, body_file))
    ws.put_json("out/names.json", {str(n): name for n, name in pseudo.filenames.items()})
    header_lines = set(built.prefixes)
    ws.put_json(f"parts/{TEXT_PART}.json", {
        "status": result.status,
        "reasons": result.reasons,
        "report": {**result.report, "images_retirees": state.get("images_retirees", {})},
        "body_file": body_file,
        "content_type": "text/html; charset=utf-8" if html is not None else "text/plain; charset=utf-8",
        "occurrences": [
            {**o, "fichier": "mail.json" if o["ligne"] in header_lines else body_file} for o in result.occurrences
        ],
    })
    log.info("Texte du mail pseudonymisé doc=%s statut=%s", job.document_id, result.status)
    return try_finalize(runtime, ws) or result.status


def _mail_json(mail, folder: str, pseudo, body_file: str) -> dict:
    """Contenu de mail.json : en-têtes pseudonymisés (organisations de la liste conservées)."""
    return {
        "dossier": folder,
        "date": mail.date.isoformat() if mail.date else None,
        "objet": pseudo.subject,
        "de": pseudo.headers.get("from", []),
        "a": pseudo.headers.get("to", []),
        "cc": pseudo.headers.get("cc", []),
        "repondre_a": pseudo.headers.get("reply-to", []),
        "corps": body_file,
        "pieces_jointes": [
            {"fichier": pseudo.filenames[a.index], "format": a.fmt, "integree": a.content_id is not None}
            for a in mail.attachments
        ],
    }
