"""Ré-identification d'un jeton (usage restreint, tracé dans les journaux Key Vault / Cosmos DB).

Nécessite les rôles : Key Vault Secrets User + Cosmos DB Built-in Data Reader.

    python reidentify.py "[NOM_3FA94C0B21]" --settings local.settings.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pseudo.config import Settings
from pseudo.pseudonymizer import ValueCipher
from pseudo.runtime import credential, load_keys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tokens", nargs="+")
    parser.add_argument("--settings", type=Path)
    args = parser.parse_args()
    if args.settings:
        for key, value in json.loads(args.settings.read_text(encoding="utf-8")).get("Values", {}).items():
            os.environ.setdefault(key, str(value))

    from azure.cosmos import CosmosClient

    settings = Settings.from_env()
    cred = credential()
    _, enc_key = load_keys(settings, cred)
    cipher = ValueCipher(enc_key)
    container = (
        CosmosClient(settings.cosmos_endpoint, credential=cred)
        .get_database_client(settings.cosmos_database)
        .get_container_client(settings.cosmos_mapping_container)
    )
    for token in args.tokens:
        token = token if token.startswith("[") else f"[{token}]"
        item = container.read_item(item=token.strip("[]"), partition_key=token.strip("[]"))
        print(f"{token} -> {cipher.decrypt(token, item['ciphertext'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
