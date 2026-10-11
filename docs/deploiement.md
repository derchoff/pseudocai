# Déploiement

## Prérequis

- Terraform ≥ 1.9, Azure CLI (`az login`), Azure Functions Core Tools v4 (`func`).
- Droits sur l'abonnement : **Contributor** et **User Access Administrator** (ou Owner),
  nécessaires pour créer les affectations de rôles.
- Quota Azure OpenAI pour le modèle choisi dans la région `openai_location`.
- Un compte de stockage pour l'état Terraform (`backend.hcl`).

> L'état Terraform contient les clés HMAC et AES générées. Stockez-le dans un backend chiffré
> à accès restreint, ou créez ces secrets hors Terraform (voir `infra/keyvault.tf`).

## Préparer l'abonnement (une seule fois)

**1. Enregistrer les fournisseurs de ressources.** Sans cela, l'apply échoue avec
`MissingSubscriptionRegistration` :

```powershell
foreach ($p in 'Microsoft.ManagedIdentity','Microsoft.App','Microsoft.Web','Microsoft.CognitiveServices','Microsoft.EventGrid','Microsoft.ServiceBus','Microsoft.DocumentDB','Microsoft.KeyVault','Microsoft.OperationalInsights','Microsoft.Insights','Microsoft.Storage','Microsoft.Network') { az provider register -n $p }
```

L'enregistrement prend quelques minutes. Les fournisseurs encore en cours d'enregistrement
s'affichent avec :

```powershell
az provider list --query "[?registrationState!='Registered' && contains('Microsoft.ManagedIdentity Microsoft.App Microsoft.Web Microsoft.CognitiveServices Microsoft.EventGrid Microsoft.ServiceBus Microsoft.DocumentDB Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Insights Microsoft.Storage Microsoft.Network', namespace)].{ns:namespace, etat:registrationState}" -o table
```

**2. Vérifier le quota Azure OpenAI** pour le modèle et le type de déploiement choisis.
Il faut une **limite au moins égale à `openai_capacity`** (en milliers de tokens/minute) :

```powershell
az cognitiveservices usage list -l swedencentral --query "[?contains(name.value, 'DataZoneStandard')].{quota:name.value, utilise:currentValue, limite:limit}" -o table
```

Si la limite est à 0 (`InsufficientQuota` à l'apply), trois solutions :
- **Demander du quota** : portail Azure AI Foundry, rubrique *Quotas*, ou le formulaire
  [aka.ms/oai/quotaincrease](https://aka.ms/oai/quotaincrease). C'est la bonne option pour des
  données personnelles.
- **Choisir un autre modèle** qui a déjà du quota en `DataZoneStandard` : modifier
  `openai_model_name` / `openai_model_version`. Le modèle doit prendre en charge les
  *Structured Outputs*.
- **Pour un test avec des données fictives uniquement** : `openai_deployment_sku = "GlobalStandard"`,
  dont le quota est souvent disponible par défaut. Les données peuvent alors être traitées hors
  de l'UE : à proscrire pour de vraies données personnelles.

Si le déploiement échoue malgré tout, réduire `openai_capacity` (par exemple à `10`) en
attendant le quota.

## Étapes

```powershell
copy infra/backend.hcl.example infra/backend.hcl
copy infra/terraform.tfvars.example infra/terraform.tfvars
# éditer les deux fichiers
./scripts/deploy.ps1          # terraform init/plan/apply + prompts + code
```

`deploy.ps1` enchaîne :
1. `terraform init`, `plan` puis `apply` (avec confirmation) ;
2. `publish-prompts.ps1` : copie de `prompts/*.md` vers `config/prompts/` ;
3. `func azure functionapp publish <app> --python` : build distant des dépendances.

Mises à jour partielles :

```powershell
./scripts/publish-prompts.ps1     # prompts seuls (pris en compte en moins de 5 min)
./scripts/deploy.ps1 -SkipInfra   # code + prompts
```

## Ce qui est variabilisé (et pourquoi)

| Variable | Défaut | Pourquoi la variabiliser |
|---|---|---|
| `subscription_id` | — | Obligatoire avec azurerm 5.x ; diffère selon l'environnement. |
| `project`, `environment` | `pseudo`, `dev` | Nommage et isolation dev/rec/prod. Un suffixe aléatoire garantit l'unicité globale des noms. |
| `location` | `francecentral` | Résidence des données. |
| `openai_location` | `swedencentral` | Les modèles ne sont pas disponibles dans toutes les régions. |
| `openai_model_name` / `_version` | `gpt-5-mini` / `2025-08-07` | Le modèle évolue (dépréciations, nouveaux modèles). **Vérifier sa disponibilité** dans la région avant d'appliquer. |
| `openai_deployment_sku` | `DataZoneStandard` | Traitement des données dans la zone UE. `Standard` reste régional, `GlobalStandard` est à proscrire pour des données personnelles. |
| `openai_capacity` | `100` (k tokens/min) | Dépend du quota et du volume de documents. |
| `openai_reasoning_effort` | `low` | Compromis coût / qualité. Laisser vide pour un modèle non « raisonnement ». |
| `enable_language_pii` | `true` | Filet de sécurité optionnel, avec un coût supplémentaire. |
| `enable_private_networking` | `false` | Le réseau privé est recommandé en production, mais plus coûteux (Service Bus Premium) et plus complexe à opérer. |
| `allowed_ip_ranges` | `[]` | IP du poste ou du runner CI, autorisées quand l'accès public est fermé. |
| `function_max_instances` | `10` | Limite la parallélisation, et donc la charge sur Azure OpenAI. |
| `function_instance_memory_mb` | `2048` | Les gros PDF scannés demandent de la mémoire (rastérisation). |
| `servicebus_max_delivery_count` | `5` | Nombre de tentatives avant dead-letter. |
| `delete_source_after_success` | `false` | Politique de conservation des originaux. |
| `incoming_retention_days`, `quarantine_retention_days` | `7`, `30` | Minimisation des données (RGPD). |
| `key_vault_purge_protection` | `true` | À mettre à `false` uniquement en bac à sable. |
| `reidentification_principal_ids` | `[]` | Groupe Entra autorisé à ré-identifier les jetons. |
| `pipeline_settings` | `{}` | Réglages fins du pipeline sans modifier Terraform (voir ci-dessous). |
| `tags` | `{}` | Gouvernance et refacturation. |

**Non variabilisé volontairement** :
- Noms des containers et de la file : ce sont des contrats entre le code et l'infrastructure.
- SKU du stockage (ZRS) et désactivation des clés d'accès : ce sont des exigences de sécurité.
- Rôles RBAC : principe du moindre privilège.

### Paramètres du pipeline (`pipeline_settings` → app settings)

| Clé | Défaut | Effet |
|---|---|---|
| `MAX_PAGES` | 200 | Au-delà, le document part en quarantaine. |
| `MAX_FILE_MB` | 100 | Taille maximale du fichier. |
| `MIN_NATIVE_CHARS` | 30 | En dessous, la page est **rendue** comme une page scannée (re-rastérisée). L'OCR a lieu dans tous les cas. |
| `IMAGE_COVERAGE_OCR_THRESHOLD` | 0.6 | Au-delà de cette part de la page couverte par des images, la page est **rendue** comme une page scannée. |
| `OCR_NATIVE_PAGES` | **true** | Passe aussi les pages natives dans l'OCR pour lire le texte des images (logos, tampons...). Ne le désactiver qu'en connaissance de cause. |
| `FORCE_OCR` | false | Traite toutes les pages comme des scans (rastérisation complète, plus de couche texte). |
| `OCR_MIN_CONFIDENCE` | 0.75 | Confiance OCR moyenne minimale, sinon quarantaine. |
| `OCR_RENDER_DPI` | 200 | Résolution des pages scannées en sortie. |
| `LLM_CHUNK_CHARS` | 12000 | Taille des blocs envoyés aux agents. |
| `LLM_MIN_CONFIDENCE` | 0.3 | Les entités sous ce seuil sont ignorées. |
| `VERIFICATION_ROUNDS` | 2 | Nombre de passes de l'agent vérificateur (0 pour le désactiver). |
| `TOKEN_LENGTH` | 10 | Nombre de caractères hexadécimaux du jeton (40 bits). |
| `QUARANTINE_ON_UNLOCATED` | true | Quarantaine si une entité signalée par un agent est introuvable dans le texte. |
| `LANGUAGE_PII_CATEGORIES` | `Person:PERSONNE,...` | Correspondance entre les catégories Azure AI Language et les types internes. |
| `PROMPT_CACHE_SECONDS` | 300 | Délai de rechargement des prompts. |
| `CLASSIFICATION_ENABLED` | true | Classe chaque document par type et range les sorties dans un sous-dossier. Si `false`, les sorties sont écrites à la racine des containers. |
| `CLASSIFICATION_MIN_CONFIDENCE` | 0.6 | Confiance minimale du type principal. En dessous, le document va dans `non-classe/`. |
| `CLASSIFICATION_SECONDARY_MIN_CONFIDENCE` | 0.3 | Confiance minimale pour conserver un type secondaire dans le rapport, la trace et les métadonnées. |
| `CLASSIFICATION_MAX_CHARS` | 6000 | Nombre de caractères du texte pseudonymisé envoyés à l'agent de classification. |
| `UNCLASSIFIED_FOLDER` | non-classe | Nom du dossier des documents dont le type n'a pas pu être déterminé. |

## Mode réseau privé

Avec `enable_private_networking = true` :
- Un VNet est créé avec deux sous-réseaux : intégration Flex et private endpoints.
- Les zones DNS privées et les private endpoints sont créés pour le stockage
  (blob/queue/table), Key Vault, Azure OpenAI, Document Intelligence, Language, Service Bus
  et Cosmos DB.
- Les accès publics sont fermés, sauf pour `allowed_ip_ranges`.
- Service Bus passe en **Premium**. Event Grid continue de livrer les événements grâce à
  l'option *trusted services* et à son identité managée.

Terraform (création des secrets) et `publish-prompts.ps1` doivent alors s'exécuter depuis une
IP autorisée ou depuis un runner situé dans le VNet.

## Vérifications après déploiement

```powershell
$rg = terraform "-chdir=infra" output "-raw" resource_group_name
$app = terraform "-chdir=infra" output "-raw" function_app_name
az functionapp config appsettings list -g $rg -n $app --query "[?starts_with(name,'AzureWebJobsStorage')].name" -o tsv
az functionapp function list -g $rg -n $app --query "[].name" -o tsv
./scripts/upload-test.ps1 -Path ./exemple.pdf
```

- La première commande doit lister **uniquement** `AzureWebJobsStorage__accountName`,
  `__credential` et `__clientId`, **sans** `AzureWebJobsStorage` seul.
- La deuxième doit afficher `pseudonymise_pdf`.

> **Particularité du provider azurerm 5.x** : pour une Function Flex Consumption, il écrit
> toujours un paramètre `AzureWebJobsStorage` avec une clé vide. Ce paramètre est prioritaire sur
> l'identité managée et empêche la Function de démarrer (« app unhealthy »). La ressource
> `terraform_data.remove_webjobs_storage_connection_string` ([infra/function.tf](../infra/function.tf))
> le supprime après chaque création ou modification de la Function, via Azure CLI. Le poste ou le
> runner qui exécute Terraform doit donc avoir `az` installé et connecté.
