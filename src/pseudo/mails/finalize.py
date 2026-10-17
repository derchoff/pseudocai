"""Finalisation d'un mail, quand toutes ses pièces sont terminées : classement de l'ensemble puis
publication du dossier, ou quarantaine du mail entier (tout ou rien).

    pseudonymised/<type>/<chemin>/<nom du .eml>/   mail.json · body.html|txt · body.pseudonymise.txt ·
                                                   pièces et <pièce>.pseudonymise.txt · rapport.json
    quarantine/<type>/<chemin>/<nom du .eml>/      <mail d'origine>.eml · motif.json

Les parties d'un mail : le texte (en-têtes, objet, noms des pièces, corps ; voir text.py) et chaque
pièce jointe. Chaque tâche qui termine une partie appelle try_finalize. Seule celle qui voit toutes
les parties terminées ET prend le verrou publie : la dernière partie terminée voit toujours les
autres, puisque chacune écrit son marqueur avant de regarder.

Une fois la trace du mail écrite (publication ou quarantaine), son JSON de métadonnées de la GED est
rattaché s'il attend déjà dans work/json-metadata/ (voir json_jobs.py).
"""

from __future__ import annotations

import logging
import posixpath
from dataclasses import replace

from ..classification import STATUT_NON_APPLICABLE, Classification, unclassified
from ..formats import CONTENT_TYPES
from ..jobs import Job
from ..outputs import (
    OutputFile,
    StorageContext,
    _finish,
    _json,
    _now,
    _occurrence_items,
    _write,
    job_source_blob,
    previous_trace,
)
from ..pipeline import classify_text
from ..json_jobs import attach_json_metadata
from .workspace import BODY_TEXT_FILE, MailWorkspace

TEXT_PART = "texte"  # partie « texte du mail » : marqueur parts/texte.json

log = logging.getLogger(__name__)

STAGE_MAIL = "mail"


def try_finalize(runtime, ws: MailWorkspace) -> str | None:
    """Publie le mail si toutes ses pièces sont terminées et que le verrou est libre ; renvoie le statut."""
    state = ws.get_json("state.json")
    parts = {p["index"]: p for p in state["parts"]}
    markers = {n: ws.get_json(f"parts/{n}.json") for n in parts if ws.exists(f"parts/{n}.json")}
    if len(markers) < len(parts) or not ws.exists(f"parts/{TEXT_PART}.json") or not ws.acquire_lock():
        return None
    try:
        return _finalize(runtime, ws, state, markers)
    except Exception:
        ws.release_lock()  # le prochain essai du message pourra publier
        raise


def _finalize(runtime, ws: MailWorkspace, state: dict, markers: dict[int, dict]) -> str:
    """Classe le mail, puis publie le dossier ou le met en quarantaine ; nettoie work/."""
    settings = runtime.settings
    job = Job.from_json(state["job"])
    previous = previous_trace(runtime, job)
    text = ws.get_json(f"parts/{TEXT_PART}.json")
    names = ws.get_json("out/names.json") if ws.exists("out/names.json") else {}
    texts = [ws.get("text/body.txt").decode("utf-8")]
    texts += [ws.get(f"text/{n}.txt").decode("utf-8") for n in sorted(markers) if ws.exists(f"text/{n}.txt")]
    classification = classify_text("\n\n".join(texts), settings, runtime.services)

    pieces = []
    reasons = list(text["reasons"])
    for part in state["parts"]:
        part["name"] = names.get(str(part["index"]), f"piece-{part['index']}")
        marker = markers[part["index"]]
        reasons += marker["reasons"]
        pieces.append(
            {"fichier": part["name"], "format": part["format"], "integree": part["inline"],
             "statut": marker["status"], "motifs": marker["reasons"], "rapport": _without_classification(marker.get("report", {}))}
        )
    report = {
        "dossier": state["folder"],
        "corps": _without_classification(text["report"]),
        "pieces_jointes": pieces,
        "classification": classification.to_dict(),
        "mode_sortie": settings.output_mode,
    }
    if reasons:
        original = job_source_blob(runtime, job).download_blob().readall()
        quarantine_mail(
            runtime, job, original, state["folder"], sorted(set(reasons)), report, classification, previous, from_finalize=True
        )
        status = "quarantine"
    else:
        _publish(runtime, ws, job, state, text, markers, report, classification, previous)
        status = "ok"
    ws.delete_all()
    attach_json_metadata(runtime, job.document_id, job.source_container, job.source_name)
    return status


def _publish(
    runtime, ws, job: Job, state: dict, text: dict, markers: dict, report: dict, classification: Classification, previous
) -> None:
    """Écrit le dossier pseudonymisé et les occurrences (ré-identification par fichier et position)."""
    settings = runtime.settings
    folder = posixpath.join(classification.dossier, state["folder"]) if classification.dossier else state["folder"]
    report = {**report, "statut": "ok", "motifs": [], "date": _now()}
    files = [
        OutputFile(f"{folder}/mail.json", ws.get("out/mail.json"), "application/json"),
        OutputFile(f"{folder}/{text['body_file']}", ws.get(f"out/{text['body_file']}"), text["content_type"]),
    ]
    text_type = "text/plain; charset=utf-8"
    if ws.exists(f"out/{BODY_TEXT_FILE}"):  # absent pour un mail découpé par une version précédente
        files.append(OutputFile(f"{folder}/{BODY_TEXT_FILE}", ws.get(f"out/{BODY_TEXT_FILE}"), text_type))
    used = {f.name.rsplit("/", 1)[1] for f in files} | {"rapport.json"} | {p["name"] for p in state["parts"]}
    for part in state["parts"]:
        files.append(OutputFile(f"{folder}/{part['name']}", ws.get(f"out/part-{part['index']}"), CONTENT_TYPES[part["format"]]))
        if ws.exists(f"text/{part['index']}.publie.txt"):
            name = _text_name(part["name"], used)
            files.append(OutputFile(f"{folder}/{name}", ws.get(f"text/{part['index']}.publie.txt"), text_type))
    files.append(OutputFile(f"{folder}/rapport.json", _json(report), "application/json"))
    outputs = _write(runtime, settings.output_container, files, classification)

    # En-têtes, objet et noms des pièces sont dans mail.json ; le reste dans le corps.
    items = []
    for fichier in ("mail.json", text["body_file"]):
        occurrences = [o for o in text["occurrences"] if o.get("fichier") == fichier]
        items += _occurrence_items(
            replace(job, job_id=f"{job.job_id}-{fichier}"), f"{settings.output_container}/{folder}/{fichier}", occurrences, None
        )
    runtime.mapping_store.save_occurrences(job.document_id, items)
    for part in state["parts"]:
        marker = markers[part["index"]]
        part_job = Job(
            job_id=f"{job.job_id}-{part['index']}", document_id=part["document_id"],
            source_container=job.source_container, source_name=job.source_name, source_format=part["format"],
            origin="mail", received_at=job.received_at,
        )
        path = f"{settings.output_container}/{folder}/{part['name']}"
        items = _occurrence_items(part_job, path, marker.get("occurrences", []), marker.get("image_dpi"))
        runtime.mapping_store.save_occurrences(part["document_id"], items)
    _finish(runtime, job, "ok", report, classification, outputs, previous, STAGE_MAIL)
    log.info("Mail pseudonymisé doc=%s type=%s pieces=%d", job.document_id, classification.principal, len(state["parts"]))


def quarantine_mail(
    ctx: StorageContext,
    job: Job,
    original: bytes,
    folder_name: str,
    reasons: list[str],
    report: dict,
    classification: Classification | None,
    previous: dict | None,
    from_finalize: bool = False,
) -> list[str]:
    """Met le mail entier en quarantaine : .eml d'origine et motif, dans le dossier du mail.
    Rattache ensuite son JSON de métadonnées, sauf depuis la finalisation (qui le fait elle-même,
    après avoir nettoyé le dossier de travail)."""
    settings = ctx.settings
    classification = classification or unclassified(STATUT_NON_APPLICABLE, settings.unclassified_folder)
    folder = posixpath.join(classification.dossier, folder_name) if classification.dossier else folder_name
    motif = {
        "motifs": reasons,
        "etape": STAGE_MAIL,
        "origine": job.origin,
        "format": job.source_format,
        "classification": classification.to_dict(),
        "rapport": report,
        "date": _now(),
    }
    files = [
        OutputFile(f"{folder}/{posixpath.basename(job.source_name)}", original, CONTENT_TYPES["eml"]),
        OutputFile(f"{folder}/motif.json", _json(motif), "application/json"),
    ]
    outputs = _write(ctx, settings.quarantine_container, files, classification)
    _finish(ctx, job, "quarantine", {**report, "motifs": reasons}, classification, outputs, previous, STAGE_MAIL)
    if not from_finalize:
        attach_json_metadata(ctx, job.document_id, job.source_container, job.source_name)
    log.warning("Mail en quarantaine doc=%s type=%s motifs=%s", job.document_id, classification.principal, reasons)
    return outputs


def _text_name(part_name: str, used: set[str]) -> str:
    """Nom du texte d'une pièce : « facture.pseudonymise.txt », comme pour un document seul ; si
    deux pièces ont le même nom sans extension (facture.pdf, facture.jpg), le nom complet sert."""
    stem, _ = posixpath.splitext(part_name)
    name = f"{stem}.pseudonymise.txt"
    if name in used:
        name = f"{part_name}.pseudonymise.txt"
    used.add(name)
    return name


def _without_classification(report: dict) -> dict:
    """Rapport d'une partie sans sa classification : le mail est classé une seule fois, en entier."""
    return {k: v for k, v in report.items() if k != "classification"}
