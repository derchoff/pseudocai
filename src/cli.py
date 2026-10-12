"""Exécution locale de la chaîne sur un PDF ou une image (mise au point des prompts, recette).

Utilise les vrais services Azure (az login) mais lit les prompts dans le dossier local et
n'écrit rien dans Cosmos DB par défaut. Pour une image, l'étape de préparation est exécutée en
mémoire (nécessite les dépendances de requirements-images.txt).

    python cli.py document.pdf --out ./sortie --settings local.settings.json
    python cli.py photo.jpg --out ./sortie --settings local.settings.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from types import SimpleNamespace

from pseudo.config import Settings
from pseudo.formats import PDF, format_from_name, is_image
from pseudo.jobs import ORIGIN_IMAGE, Job, PreparedImage
from pseudo.mapping_store import JsonFileMappingStore, NullMappingStore
from pseudo.pipeline import PipelineInput, run_pipeline
from pseudo.runtime import build_runtime

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    """Pseudonymise un PDF ou une image en local et écrit les sorties dans --out. Renvoie le code de sortie."""
    parser = argparse.ArgumentParser(description="Pseudonymise localement un PDF ou une image")
    parser.add_argument("document", type=Path)
    parser.add_argument("--out", type=Path, default=Path("sortie"))
    parser.add_argument("--settings", type=Path, help="local.settings.json dont les 'Values' sont chargées")
    parser.add_argument("--prompts-dir", type=Path, default=ROOT / "prompts")
    parser.add_argument("--mapping-file", type=Path, help="Fichier JSON local de correspondances (chiffrées)")
    parser.add_argument("--no-language", action="store_true", help="Désactive Azure AI Language")
    parser.add_argument("--no-verification", action="store_true", help="Désactive l'agent vérificateur")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.settings:
        values = json.loads(args.settings.read_text(encoding="utf-8")).get("Values", {})
        for key, value in values.items():
            os.environ.setdefault(key, str(value))

    settings = Settings.from_env()
    store = JsonFileMappingStore(args.mapping_file) if args.mapping_file else NullMappingStore()
    runtime = build_runtime(
        settings,
        prompts_dir=str(args.prompts_dir),
        mapping_store=store,
        enable_language=not args.no_language,
        enable_verification=not args.no_verification,
    )

    data = args.document.read_bytes()
    fmt = format_from_name(args.document.name)
    if fmt == PDF:
        pipeline_input = PipelineInput(pdf=data, source={"origine": "pdf", "format": fmt})
    elif is_image(fmt):
        pipeline_input = _prepare_image_in_memory(data, fmt, settings, runtime.services.ocr)
        if pipeline_input is None:
            return 2
    else:
        print("Format non pris en charge")
        return 2

    result = run_pipeline(pipeline_input, settings, runtime.services)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.document.stem
    if result.output:
        (args.out / f"{stem}.pseudonymise{args.document.suffix}").write_bytes(result.output)
    if result.text:
        (args.out / f"{stem}.pseudonymise.txt").write_text(result.text, encoding="utf-8")
    (args.out / f"{stem}.rapport.json").write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.status == "ok":
        store.save(result.records)
    classification = result.classification
    if classification is not None:
        scores = ", ".join(f"{t.type} {t.confidence:.2f}" for t in classification.types) or "aucun"
        print(f"Type : {classification.principal} (dossier « {classification.dossier or '-'} » ; {scores})")
    print(f"Statut : {result.status} {result.reasons or ''} -> {args.out}")
    return 0 if result.status == "ok" else 2


def _prepare_image_in_memory(data: bytes, fmt: str, settings: Settings, ocr) -> PipelineInput | None:
    """Étape 2 exécutée en local : les fichiers « work/ » restent en mémoire."""
    from pseudo.images.prepare import prepare_image
    from pseudo.prepared_image import load_image_input

    artifacts = prepare_image(data, fmt, settings)
    if artifacts.reasons:
        print(f"Image refusée à la préparation : {artifacts.reasons}")
        return None
    files = {"render.pdf": artifacts.render_pdf, "transforms.json": artifacts.transforms}
    if artifacts.ocr_pdf:
        files["ocr.pdf"] = artifacts.ocr_pdf
    work = SimpleNamespace(
        get_blob_client=lambda name: SimpleNamespace(
            download_blob=lambda: SimpleNamespace(readall=lambda: files[name])
        )
    )
    job = Job.new("local://" + fmt, "local", "document", fmt, ORIGIN_IMAGE)
    job.prepared = PreparedImage(
        render_pdf="render.pdf",
        transforms="transforms.json",
        ocr_pdf="ocr.pdf" if artifacts.ocr_pdf else None,
        preparation=artifacts.preparation,
    )
    return load_image_input(job, data, work, ocr, settings)


if __name__ == "__main__":
    raise SystemExit(main())
