"""Ré-identification (usage restreint, tracé dans les journaux Key Vault / Cosmos DB).

Nécessite les rôles : Key Vault Secrets User + Cosmos DB Built-in Data Reader.

Trois usages :

    # 1. Un ou plusieurs jetons -> valeurs d'origine
    python reidentify.py jetons "[NOM_3FA94C0B21]" PRENOM_77D0A1E2C4 --settings local.settings.json

    # 2. Toutes les valeurs remplacées d'un document publié (chemin dans pseudonymised/)
    python reidentify.py document "pseudonymised/year=2026/month=10/day=18/hour=14/domain=dentaire/lot1/facture.pdf" --settings local.settings.json

    # 3. Une valeur fictive précise d'un document (éventuellement à une page donnée)
    python reidentify.py document "pseudonymised/year=2026/month=10/day=18/hour=14/domain=dentaire/lot1/facture.pdf" --valeur "MEYER" --page 1

Chaîne de résolution : occurrences (document, page, position, valeur fictive) -> jeton ->
mappings (valeur d'origine chiffrée) -> déchiffrement avec la clé de Key Vault.
Une valeur fictive seule peut être ambiguë (deux personnes peuvent recevoir le même faux nom) :
on part donc toujours du document.
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
    """Affiche les valeurs d'origine de jetons, ou de toutes les valeurs remplacées d'un document publié."""
    parser = argparse.ArgumentParser(description="Ré-identification de valeurs pseudonymisées")
    sub = parser.add_subparsers(dest="mode", required=True)
    tokens = sub.add_parser("jetons", help="jetons -> valeurs d'origine")
    tokens.add_argument("tokens", nargs="+")
    document = sub.add_parser("document", help="valeurs remplacées d'un document publié")
    document.add_argument("chemin", help="chemin du document publié, ex. pseudonymised/year=2026/month=10/day=18/hour=14/domain=dentaire/lot1/facture.pdf")
    document.add_argument("--valeur", help="ne garder que cette valeur fictive")
    document.add_argument("--page", type=int, help="ne garder que cette page (à partir de 1)")
    for p in (tokens, document):
        p.add_argument("--settings", type=Path, help="local.settings.json dont les 'Values' sont chargées")
    args = parser.parse_args()

    if args.settings:
        for key, value in json.loads(args.settings.read_text(encoding="utf-8")).get("Values", {}).items():
            os.environ.setdefault(key, str(value))

    from azure.cosmos import CosmosClient

    settings = Settings.from_env()
    cred = credential()
    _, enc_key = load_keys(settings, cred)
    cipher = ValueCipher(enc_key)
    database = CosmosClient(settings.cosmos_endpoint, credential=cred).get_database_client(settings.cosmos_database)
    mappings = database.get_container_client(settings.cosmos_mapping_container)

    def original(token: str) -> str:
        """Valeur d'origine d'un jeton : lecture dans Cosmos DB puis déchiffrement."""
        token_id = token.strip("[]")
        item = mappings.read_item(item=token_id, partition_key=token_id)
        return cipher.decrypt(f"[{token_id}]", item["ciphertext"])

    if args.mode == "jetons":
        for token in args.tokens:
            print(f"[{token.strip('[]')}] -> {original(token)}")
        return 0

    occurrences = database.get_container_client(settings.cosmos_occurrence_container)
    query = "SELECT * FROM c WHERE c.document = @doc"
    parameters = [{"name": "@doc", "value": args.chemin}]
    if args.valeur is not None:
        query += " AND c.valeur_fictive = @valeur"
        parameters.append({"name": "@valeur", "value": args.valeur})
    if args.page is not None:
        query += " AND c.page = @page"
        parameters.append({"name": "@page", "value": args.page})
    found = list(occurrences.query_items(query=query, parameters=parameters, enable_cross_partition_query=True))
    if not found:
        print("Aucune occurrence trouvée pour ce document.")
        return 1
    for occ in sorted(found, key=lambda o: (o["page"], o["position"][1], o["position"][0])):
        shown = occ.get("valeur_fictive") or f"[{occ['jeton']}]"
        print(f"page {occ['page']} {occ['position']} {occ['type']:<15} {shown!r} -> {original(occ['jeton'])!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
