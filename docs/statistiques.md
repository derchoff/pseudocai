# Statistiques de traitement

Chaque traitement écrit une **fiche** dans le container Cosmos DB `statistiques`. Le script
[`scripts/statistiques.py`](../scripts/statistiques.py) en tire, sur une période :

- les **pages traitées** et les pages par document (mails et pièces jointes compris, quarantaine comprise) ;
- les pages **réellement passées par l'OCR** et **par les agents IA** ;
- le **pourcentage de mise en quarantaine** ;
- la répartition **par type** de document et par **type MIME** ;
- les **tours de vérification** ;
- la **consommation** (OCR, agents IA, Azure AI Language) et, avec les prix, le **coût par page**.

Le script affiche ces tableaux à l'écran et les écrit dans un classeur Excel.

## Principe : une fiche par traitement

| Règle | Détail |
|---|---|
| **Une fiche par traitement** | Un document seul : une fiche. Un mail : une fiche pour le mail (son texte et son classement), plus une par pièce jointe. Un JSON de métadonnées de la GED : une fiche « annexe ». |
| **Période = date de dépose** | La date et l'heure de dépose du document dans `incoming/` (heure de Paris, réglage `DEPOSIT_TIMEZONE`), comme les dossiers `year=/month=/day=/hour=` des sorties. Un mail déposé le 18 octobre et traité le 19 compte pour le 18. |
| **Retraitements comptés** | L'identifiant de la fiche est celui du job. Un message relivré par Service Bus réécrit la même fiche : pas de double compte. Un document **retraité ou redéposé** crée un nouveau job, donc une nouvelle fiche, car il consomme à nouveau l'OCR et les agents IA. Le coût par page reflète ainsi la dépense réelle. |
| **Sa consommation seulement** | Chaque fiche porte la consommation de **son** traitement. Celle du mail couvre le texte du mail et le classement du mail entier. Celle de chaque pièce couvre la pièce. Les totaux s'additionnent sans double compte. |
| **Aucune donnée personnelle** | Ni nom de fichier, ni valeur détectée : seulement des empreintes (`documentId`), des catégories, des compteurs et l'extension. |
| **Conservation illimitée** | Pas d'expiration (TTL) sur le container. Les traces, elles, sont gardées 1 an. |
| **Jamais bloquant** | Une erreur d'écriture d'une fiche est journalisée (`Fiches de statistiques non écrites`), sans effet sur le document déjà publié. |

Un traitement interrompu par une erreur technique puis réessayé reprend le même job. La fiche
décrit la tentative qui aboutit : la consommation d'une tentative en échec n'y figure pas.

## Contenu d'une fiche

```json
{
  "id": "7f0c…",                 // job ; pièce de mail : <job du mail>-p<n°>
  "jour": "2026-10-18",          // clé de partition (jour de dépose)
  "year": 2026, "month": 10, "day": 18, "hour": 14,
  "niveau": "document",          // document | piece | annexe
  "categorie": "pdf",            // voir ci-dessous
  "mime": "application/pdf",
  "extension": ".pdf",
  "documentId": "1a2b3c4d5e6f7a8b", "jobId": "7f0c…", "mailJobId": null,
  "statut": "ok",                // ok | quarantine | refusee | retiree | non_traitee
  "motifs": [],
  "domain": "dentaire",          // dossier du type reconnu (domain=)
  "pages": 3,
  "toursVerification": 1,
  "residus": false,              // true = encore des résidus après le dernier tour
  "consommation": {
    "pages_ocr": 3,
    "pages_ia": 3,
    "language": {"enregistrements": 2, "caracteres": 1450},
    "ia": {
      "detection":      {"appels": 1, "jetons_entree": 5200, "jetons_sortie": 640, "jetons_raisonnement": 384},
      "verification":   {"appels": 1, "jetons_entree": 4900, "jetons_sortie": 210, "jetons_raisonnement": 192},
      "classification": {"appels": 1, "jetons_entree": 2100, "jetons_sortie": 90,  "jetons_raisonnement": 64}
    }
  },
  "deposeLe": "2026-10-18T12:05:31+00:00",
  "traiteLe": "2026-10-18T12:07:41+00:00"
}
```

| Catégorie | Niveau | Ce que c'est |
|---|---|---|
| `pdf` | document | PDF hors mail |
| `image` | document | Image hors mail (type MIME : `image/jpeg`, `image/png`, `image/heif`…) |
| `mail` | document | Mail `.eml` : son texte (en-têtes, objet, corps) et son classement. `pages` = 0 : les pages sont dans les fiches des pièces. |
| `autre` | document | Fichier refusé au routage (format non pris en charge) : `application/zip`… |
| `pdf_mail` | piece | PDF contenu dans un mail |
| `image_mail` | piece | Image contenue dans un mail (y compris les images **retirées** : GIF, logos, icônes) |
| `autre_mail` | piece | Autre pièce d'un mail : Word, ZIP, mail joint… (statut `refusee`) |
| `json` | annexe | JSON de métadonnées de la GED (`<document>.json`) : pas un document, mais il consomme les agents IA |

Le statut d'une pièce de mail peut prendre trois valeurs de plus que `ok` / `quarantine` :

| Statut | Signification |
|---|---|
| `refusee` | Pièce non prise en charge : le mail entier part en quarantaine dès le découpage. |
| `non_traitee` | Pièce acceptée, mais son mail est parti en quarantaine au découpage à cause d'une autre pièce. |
| `retiree` | Image inutile retirée du mail (GIF, image de moins de 300 px, image intégrée illisible), sans quarantaine. |

### Mesures

| Mesure | Source |
|---|---|
| `pages` | Pages du PDF ouvert par le pipeline (une image compte une page par image). Une image refusée à la préparation compte les pages préparées. Un fichier refusé avant ouverture (format inconnu, PDF chiffré) compte 0. |
| `consommation.pages_ocr` | Pages **envoyées** à Document Intelligence, c'est-à-dire facturées. Avec `OCR_NATIVE_PAGES=true` (défaut), les pages natives passent aussi à l'OCR pour lire le texte de leurs images. |
| `consommation.pages_ia` | Pages d'un PDF ou d'une image dont le texte a été **envoyé aux agents IA** : celles qui ont au moins une ligne de texte (une page blanche n'est pas envoyée). 0 si le document est arrêté avant l'IA : écriture manuscrite, PDF chiffré ou illisible, image floue, pièce refusée ou retirée. Le texte d'un mail et un JSON passent par l'IA sans avoir de page : ils sont comptés à part (« Textes passés par les agents IA »). |
| `consommation.ia.*` | Jetons rendus par Azure OpenAI pour chaque appel : `jetons_sortie` **comprend** `jetons_raisonnement`. Les jetons d'entrée servis par le cache de prompt ne sont pas distingués : le coût calculé est un majorant. |
| `consommation.language` | Enregistrements facturés par Azure AI Language (un par tranche de 1 000 caractères de chaque texte envoyé). |
| `toursVerification`, `residus` | Tours de l'agent vérificateur et motif `residus_apres_verification`. Une pièce, le texte d'un mail et un JSON ont chacun leurs tours. |

## Lancer le script

Il faut :
- la CLI Azure connectée (`az login`) ;
- le rôle **Cosmos DB Built-in Data Reader** sur le container `statistiques`. Ajoutez votre Object ID Entra à `statistics_reader_principal_ids` dans `terraform.tfvars`, puis appliquez Terraform. Ce rôle donne accès à ce **seul** container, sans les correspondances ni les occurrences ;
- l'accès réseau au compte Cosmos DB : votre adresse IP dans `allowed_ip_ranges` si le déploiement est privé.

Le nom du compte Cosmos DB est donné par :

```bash
terraform -chdir=infra output -raw cosmos_account_name
```

Statistiques d'octobre 2026, par jour, avec un classeur Excel :

```bash
uv run --python 3.12 --with azure-cosmos --with azure-identity --with openpyxl python scripts/statistiques.py --cosmos cosmos-pseudo-dev-ab12 --debut 2026-10-01 --fin 2026-10-31
```

Même période, regroupée par mois, avec les prix pour estimer le coût par page :

```bash
uv run --python 3.12 --with azure-cosmos --with azure-identity --with openpyxl python scripts/statistiques.py --cosmos cosmos-pseudo-dev-ab12 --debut 2026-01-01 --fin 2026-12-31 --par mois --prix-ocr 1.5 --prix-entree 0.25 --prix-sortie 2 --prix-language 1 --excel echantillon/statistiques-2026.xlsx
```

Les prix ci-dessus ne sont que des exemples : prenez ceux de votre contrat ou de la page de tarifs
Azure (Document Intelligence « Read », modèle Azure OpenAI déployé, Azure AI Language PII).

| Paramètre | Défaut | Rôle |
|---|---|---|
| `--cosmos` | (obligatoire) | Compte Cosmos DB : nom (`cosmos-pseudo-dev-ab12`) ou URL `https://….documents.azure.com:443/`. |
| `--fiches` | | À la place de `--cosmos` : export JSON des fiches (une liste), pour travailler hors connexion. |
| `--base`, `--container` | `pseudonymisation`, `statistiques` | Base et container Cosmos DB. |
| `--debut`, `--fin` | 1er du mois, aujourd'hui | Jours de **dépose** (AAAA-MM-JJ), bornes comprises. |
| `--par` | `jour` | Regroupement des tableaux : `jour`, `mois` ou `annee`. |
| `--excel` | `statistiques_<debut>_<fin>.xlsx` | Classeur produit. |
| `--sans-excel` | | Affichage à l'écran seulement. |
| `--prix-ocr` | 0 | Prix de 1 000 pages Document Intelligence. |
| `--prix-entree`, `--prix-sortie` | 0 | Prix de 1 million de jetons d'entrée et de sortie du modèle. |
| `--prix-language` | 0 | Prix de 1 000 enregistrements Azure AI Language. |

Sans aucun prix, les colonnes de coût restent vides.

## Tableaux produits

L'écran affiche les cinq premiers tableaux. Le classeur Excel les contient tous, un par onglet.

| Onglet | Contenu |
|---|---|
| **Synthèse** | Documents déposés (un mail compte pour un), dont mails ; publiés, en quarantaine, **% de quarantaine** ; documents traités plus d'une fois ; pièces de mails traitées, en quarantaine, refusées, images retirées ; JSON ; **pages traitées**, pages par document (moyenne, médiane, maximum) ; **pages passées par l'OCR**, **pages passées par les agents IA**, textes passés par l'IA (corps de mail, JSON) ; appels et jetons IA ; enregistrements Language ; **coût estimé** et **coût par page traitée**. |
| **Par période** | Par jour, mois ou année : documents, mails, quarantaine (nombre et %), pages, pages OCR, pages IA, jetons, coût et coût par page. |
| **Par type** | Par catégorie et type MIME : nombre, publiés, en quarantaine, refusés, retirés, non traités, % de quarantaine, pages, pages OCR, pages IA, jetons, coût. Les pièces de mails y sont détaillées (PDF, images, autres) : on y lit combien de pages de pièces jointes sont réellement passées par l'OCR et par l'IA. |
| **Tours de vérification** | Par période, sur les textes vérifiés (documents, textes de mails, pièces, JSON) : nombre à 1, 2, 3, 4 tours et plus, moyenne, maximum, **résidus après le dernier tour** (nombre et %). |
| **Agents IA** | Appels et jetons de la détection, de la vérification et de la classification, avec leur coût. |
| **Documents** (Excel seulement) | Une ligne par document déposé : jour, heure, empreinte, catégorie, type MIME, statut, motifs, domaine, pages (pièces comprises), nombre de pièces, tours, pages OCR, pages IA, appels et jetons (pièces et JSON compris), coût. |

Définitions :
- **Pages passées par l'OCR** : pages envoyées à Document Intelligence (facturées), pièces de mails comprises.
- **Pages passées par les agents IA** : pages dont le texte a été envoyé à l'agent de détection (puis au vérificateur), pièces de mails comprises. Une page peut passer par l'IA sans OCR (PDF natif avec `OCR_NATIVE_PAGES=false`) ou par l'OCR sans IA (écriture manuscrite détectée).
- **% de quarantaine** : documents en quarantaine / documents terminés (publiés + quarantaine), un mail comptant pour un.
- **Pages traitées** : somme des pages de toutes les fiches. Un mail apporte les pages de ses pièces ; un document retraité compte à chaque traitement.
- **Coût par page traitée** : coût estimé de la période / pages traitées.

## Requêtes directes (Data Explorer)

Les fiches se lisent aussi dans l'explorateur de données du portail Azure (container `statistiques`) :

```sql
-- Pages traitées et documents en quarantaine, par jour d'octobre 2026
SELECT c.jour, SUM(c.pages) AS pages, COUNT(1) AS fiches
FROM c WHERE c.jour >= "2026-10-01" AND c.jour <= "2026-10-31"
GROUP BY c.jour

-- Répartition par type MIME des documents déposés hors mail
SELECT c.categorie, c.mime, COUNT(1) AS nombre
FROM c WHERE c.niveau = "document" AND c.year = 2026 AND c.month = 10
GROUP BY c.categorie, c.mime

-- Textes encore en doute après le dernier tour de vérification
SELECT c.jour, c.categorie, c.toursVerification FROM c WHERE c.residus = true
```

## Code

| Fichier | Rôle |
|---|---|
| [statistics/usage.py](../src/pseudo/statistics/usage.py) | Consommation d'un traitement (rapport `consommation`), relevée par le pipeline à chaque document : pages OCR, Language, appels et jetons de chaque agent. |
| [statistics/statistics.py](../src/pseudo/statistics/statistics.py) | Construction et écriture des fiches, appelée après la trace : publication ou quarantaine d'un document, d'un mail (et de ses pièces), d'un JSON. |
| [mapping_store.py](../src/pseudo/mapping_store.py) | `save_statistics` : écriture (`upsert`) dans le container. |
| [infra/cosmos.tf](../infra/cosmos.tf) | Container `statistiques` (partition `/jour`, sans TTL) et rôle de lecture limité à ce container. |
| [scripts/statistiques.py](../scripts/statistiques.py) | Lecture des fiches de la période, calculs, affichage et classeur Excel. |
