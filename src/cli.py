"""Exécution locale du pipeline sur un PDF (mise au point des prompts, recette).

Utilise les vrais services Azure (az login) mais lit les prompts dans le dossier local et
n'écrit rien dans Cosmos DB par défaut.

    python cli.py document.pdf --out ./sortie --settings local.settings.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from pseudo.config import Settings
from pseudo.mapping_store import JsonFileMappingStore, NullMappingStore
from pseudo.pipeline import run_pipeline
from pseudo.runtime import build_runtime

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Pseudonymise un PDF localement")
    parser.add_argument("pdf", type=Path)
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

    result = run_pipeline(args.pdf.read_bytes(), settings, runtime.services)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.pdf.stem
    if result.pdf:
        (args.out / f"{stem}.pseudonymise.pdf").write_bytes(result.pdf)
    if result.text:
        (args.out / f"{stem}.pseudonymise.txt").write_text(result.text, encoding="utf-8")
    (args.out / f"{stem}.rapport.json").write_text(
        json.dumps(result.report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result.status == "ok":
        store.save(result.records)
    print(f"Statut : {result.status} {result.reasons or ''} -> {args.out}")
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
