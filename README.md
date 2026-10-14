# Service de pseudonymisation de documents sur Azure

Les documents déposés dans un compte de stockage, **PDF ou images**, sont automatiquement
pseudonymisés, classés par type et rangés.

Les images peuvent être des photos de smartphone ou des scans : JPEG, PNG, HEIC, TIFF, WebP, BMP.

Données traitées : **nom, prénom, date de naissance, NIR (n° de sécurité sociale), e-mail,
adresse postale, téléphone**, ainsi que, pour les cartes grises, **immatriculation, numéro de
châssis (VIN) et numéro de formule**. La liste se configure dans
[prompts/champs-a-pseudonymiser.md](prompts/champs-a-pseudonymiser.md).

Par défaut, chaque valeur est remplacée par une **valeur fictive réaliste**, au même format :
`M. DUPONT Jean, tél. 06 12 34 56 78` devient `M. MEYER Bernard, tél. 06 39 98 07 15`. Le document
reste ainsi lisible par un OCR et exploitable en test. Un mode « jetons » (`[NOM_3FA94C0B21]`)
reste disponible. Dans les deux cas, une table de correspondance chiffrée (Cosmos DB) permet une
ré-identification contrôlée, par document et position. Il s'agit donc d'une pseudonymisation au
sens du RGPD, pas d'une anonymisation.

```
incoming/ ─► Event Grid ─► [incoming-events] ─► ① Routage (Function principale)
                                                   │ format déduit de l'extension     
                                                   ├─► [pdf-jobs] ──────────────────────────────┐
                                                   └─► [image-jobs] ─► ② Préparation des images │
                                                                       (Function images, OpenCV) │
                                                                       work/<job>/ ─► [pdf-jobs] ┤
                                                                                                 ▼
                         ③ Pseudonymisation (Function principale) : OCR, agents IA de détection
                           et de vérification, filets regex + Language, jetons, classification,
                           caviardage ─► pseudonymised/<type>/  |  quarantine/<type>/
```

- **Le document ne bouge pas** : il reste dans `incoming/`. Seuls des **messages** (jobs)
  passent d'une étape à l'autre, avec un identifiant de corrélation.
- **Le format est déduit uniquement de l'extension.** Un fichier dont le contenu ne correspond
  pas à son extension (image renommée en `.pdf`…) n'est pas réinterprété : il part en quarantaine.
- **Un PDF**, natif ou scanné, est pseudonymisé directement. Toutes les pages passent par
  l'OCR, pour lire aussi le texte des images (logo d'un praticien, tampon d'un artisan…).
- **Une image** est d'abord préparée par une Function App dédiée :
  - orientation EXIF et contrôles qualité (flou, résolution) ;
  - copie redressée et améliorée, réservée à l'OCR.

  La sortie garde le **format et le cadrage d'origine**, sans métadonnées EXIF. Le traitement
  des images s'active par Terraform (`enable_image_processing`).
- **Un mail `.eml`** (option `enable_mail_processing`) donne un dossier par mail : en-têtes,
  corps (HTML ou texte) et pièces jointes pseudonymisés, rapport. Les organisations partenaires
  listées dans le référentiel (noms, domaines e-mail) ne sont pas pseudonymisées.
- **Chaque document est classé par type** (dentaire, optique, kinésithérapie, psychologie, carte grise…) et
  rangé dans le sous-dossier correspondant, y compris en quarantaine. Les types se déclarent
  dans [prompts/types-documents.md](prompts/types-documents.md), sans redéploiement.

## Arborescence

| Dossier | Contenu |
|---|---|
| [docs/](docs/) | [Architecture](docs/architecture.md) (diagrammes Mermaid), [déploiement](docs/deploiement.md) (dont l'[inventaire des ressources Azure](docs/deploiement.md#ressources-déployées)), [exploitation](docs/exploitation.md), [prompts](docs/prompts.md) |
| [prompts/](prompts/) | Référentiels des champs et des types de document, prompts des trois agents IA |
| [infra/](infra/) | Terraform (azurerm 5.x), module `flex_function` partagé par les deux Function Apps |
| [src/](src/) | `function_app.py` (point d'entrée des deux apps, choisi par `FUNCTION_APP_ROLE`), `functions_main.py` (routage + pseudonymisation), `functions_images.py` (préparation des images), package `pseudo`, outils `cli.py` / `reidentify.py` |
| [tests/](tests/) | Tests unitaires et de bout en bout (sans appel Azure) |
| [scripts/](scripts/) | Déploiement PowerShell |
| [exemples/](exemples/) | Factures fictives (dentiste, opticien, psychologue, kiné), photos et scans fictifs, et leurs générateurs |

## Démarrage rapide

```powershell
# 1. Infrastructure + prompts + code (les deux Function Apps si enable_image_processing = true)
copy infra/terraform.tfvars.example infra/terraform.tfvars   # puis éditer
copy infra/backend.hcl.example infra/backend.hcl             # puis éditer
./scripts/deploy.ps1

# 2. Test : déposer un document dans incoming/
./scripts/upload-test.ps1 -Path ./exemples/facture-dentiste.pdf
./scripts/upload-test.ps1 -Path ./exemples/photo-facture-dentiste.jpg
```

Tests locaux (sans Azure) :

```bash
uv run --python 3.12 --with-requirements requirements-dev.txt pytest -q tests
```

Mise au point des prompts sur un document réel (PDF ou image), depuis un poste connecté avec
`az login` :

```bash
cd src && python cli.py ../exemples/photo-facture-dentiste.jpg --out ../sortie --settings local.settings.json
```

## Licence

[MIT](LICENSE)
