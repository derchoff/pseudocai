# Service de pseudonymisation de PDF sur Azure

Les PDF déposés dans un compte de stockage sont automatiquement pseudonymisés.

Données traitées : **nom, prénom, date de naissance, NIR (n° de sécurité sociale), e-mail,
adresse postale, téléphone**.

Chaque valeur est remplacée par un jeton stable (`[NOM_3FA94C0B21]`). La table de
correspondance chiffrée permet une ré-identification contrôlée. Il s'agit donc d'une
pseudonymisation au sens du RGPD, pas d'une anonymisation.

```
Storage (incoming/) ─► Event Grid ─► Service Bus ─► Azure Function (Python)
                                                     ├─ PyMuPDF (couche texte) + OCR de toutes les pages
                                                     ├─ Agent IA de détection    (Azure OpenAI + prompt)
                                                     ├─ Filets : regex + Azure AI Language
                                                     ├─ Agent IA de vérification (Azure OpenAI + prompt)
                                                     └─ Caviardage PDF ─► pseudonymised/ | quarantine/
```

Périmètre actuel : **PDF uniquement**, qu'ils soient natifs ou scannés. Toutes les pages
passent par l'OCR, pour détecter aussi le texte contenu dans les images (logo d'un praticien,
tampon d'un artisan...). Les photos de
smartphone seront traitées dans une seconde phase (voir [architecture](docs/architecture.md#évolutions)).

## Arborescence

| Dossier | Contenu |
|---|---|
| [docs/](docs/) | [Architecture](docs/architecture.md) (diagrammes Mermaid), [déploiement](docs/deploiement.md), [exploitation](docs/exploitation.md), [prompts](docs/prompts.md) |
| [prompts/](prompts/) | Référentiel des champs et prompts des deux agents IA |
| [infra/](infra/) | Terraform (azurerm 5.x) |
| [src/](src/) | Azure Function Python + package `pseudo` + outils `cli.py` / `reidentify.py` |
| [tests/](tests/) | Tests unitaires et de bout en bout (sans appel Azure) |
| [scripts/](scripts/) | Déploiement PowerShell |

## Démarrage rapide

```powershell
# 1. Infrastructure + prompts + code
copy infra/terraform.tfvars.example infra/terraform.tfvars   # puis éditer
copy infra/backend.hcl.example infra/backend.hcl             # puis éditer
./scripts/deploy.ps1

# 2. Test
./scripts/upload-test.ps1 -Path ./mon-document.pdf
```

Tests locaux (sans Azure) :

```bash
uv run --python 3.12 --with-requirements requirements-dev.txt pytest -q tests
```

Mise au point des prompts sur un PDF réel, depuis un poste connecté avec `az login` :

```bash
cd src && python cli.py ../exemple.pdf --out ../sortie --settings local.settings.json
```

## Licence

[MIT](LICENSE)
