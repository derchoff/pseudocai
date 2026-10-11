# Architecture

## Vue d'ensemble

```mermaid
flowchart LR
    U([Dépôt PDF<br/>SFTP / API / appli]) --> IN

    subgraph ST["Storage account data (ZRS, clés désactivées)"]
        IN[(incoming/)]
        OUT[(pseudonymised/)]
        Q[(quarantine/)]
        CFG[(config/prompts/<br/>versionné)]
    end

    IN -- BlobCreated *.pdf --> EG[Event Grid<br/>System Topic]
    EG -- identité managée --> SB[[Service Bus<br/>queue pdf-events<br/>+ dead-letter]]
    SB --> F

    subgraph F["Azure Function Flex Consumption (Python 3.12)"]
        direction TB
        F1[1. Extraction<br/>PyMuPDF] --> F2[2. OCR de toutes les pages]
        F2 --> F3[3. Agent de détection]
        F3 --> F4[4. Filets de sécurité<br/>regex + Language]
        F4 --> F5[5. Jetons HMAC]
        F5 --> F6[6. Agent de vérification]
        F6 --> F7[7. Caviardage PDF]
    end

    F2 -.-> DI[Azure AI<br/>Document Intelligence]
    F3 -.-> AOAI[Azure OpenAI<br/>Data Zone EU]
    F6 -.-> AOAI
    F4 -.-> LANG[Azure AI Language<br/>PII]
    F -.lit.-> CFG
    F5 -.clés.-> KV[Key Vault<br/>HMAC + AES]
    F -.correspondances chiffrées<br/>+ traces.-> COS[(Cosmos DB<br/>serverless)]
    F7 --> OUT
    F7 -- doute --> Q
    F -.télémétrie sans PII.-> AI[Application Insights<br/>Log Analytics]
```

Toutes les liaisons utilisent l'**identité managée** de la Function (RBAC Entra ID). Aucune
clé d'accès n'est utilisée : elles sont désactivées sur le stockage, Service Bus, Cosmos DB et
les services IA.

## Déroulé d'un traitement

```mermaid
sequenceDiagram
    autonumber
    participant S as Storage incoming
    participant EG as Event Grid
    participant SB as Service Bus
    participant F as Function
    participant DI as Document Intelligence
    participant LLM as Azure OpenAI
    participant L as AI Language
    participant KV as Key Vault
    participant C as Cosmos DB
    participant O as Storage pseudonymised / quarantine

    S->>EG: BlobCreated (PutBlob / PutBlockList)
    EG->>SB: événement (livraison via identité managée)
    SB->>F: message (verrou 5 min, renouvelé)
    F->>KV: clés HMAC / AES (une fois par instance)
    F->>S: téléchargement du PDF
    F->>F: contrôles (chiffré, vide, nb pages), aplatissement formulaires/annotations
    F->>F: classification des pages : natives / scannées (mode de rendu)
    F->>DI: prebuilt-read sur TOUTES les pages
    DI-->>F: mots + polygones + confiance
    F->>F: pages natives : couche texte + texte lu dans les images (logos, tampons...)
    F->>LLM: agent de détection (prompt + lignes numérotées)
    LLM-->>F: entités JSON (schéma strict)
    F->>L: détection PII (filet de sécurité)
    F->>F: regex (NIR avec clé, e-mail, téléphone, date de naissance)
    F->>F: localisation, propagation, fusion, jetons HMAC
    loop jusqu'à VERIFICATION_ROUNDS fois
        F->>LLM: agent de vérification (texte pseudonymisé)
        LLM-->>F: résidus éventuels
    end
    alt aucun doute
        F->>C: correspondances jeton → valeur chiffrée
        F->>O: pseudonymised/ : PDF + .txt + rapport
    else doute (OCR faible, résidus, entité non localisée, PDF chiffré...)
        F->>O: quarantine/ : original + motif
    end
    F->>C: trace (statut, compteurs, empreintes des prompts)
```

## Pipeline détaillé

```mermaid
flowchart TD
    A[PDF] --> B{Ouvrable ?<br/>non chiffré ?<br/>≤ MAX_PAGES ?}
    B -- non --> QZ[[Quarantaine]]
    B -- oui --> C[bake : formulaires et annotations<br/>deviennent du contenu]
    C --> D{Pour chaque page :<br/>texte ≥ 30 car. et<br/>images < 60 % ?}
    D -- natif --> E1[Couche texte PyMuPDF<br/>positions au caractère]
    D -- natif --> E3[OCR de la page entière]
    E3 --> E4[Garde uniquement les mots OCR<br/>hors couche texte = texte des images<br/>logo, tampon, carte collée]
    D -- scanné --> E2[OCR Document Intelligence<br/>mots + polygones + confiance]
    E1 & E4 & E2 --> F[Lignes numérotées<br/>p1-l7 texte, p1-i3 image]
    F --> G[Agent IA de détection]
    F --> H[Regex : NIR clé valide, e-mail,<br/>téléphone FR, date de naissance]
    F --> I[Azure AI Language PII]
    G --> J[Localisation exacte → souple → floue]
    J & H & I --> K[Propagation des valeurs<br/>à toutes leurs occurrences]
    K --> L[Fusion : LLM > vérif > regex ><br/>propagation > Language]
    L --> M[Jetons HMAC-SHA256]
    M --> N[Agent IA de vérification<br/>sur le texte pseudonymisé]
    N -- résidus --> L
    N -- rien --> O{Doute ?}
    O -- oui --> QZ
    O -- non --> P[Caviardage]
    P --> P1[Pages natives : suppression réelle du texte<br/>+ effacement des pixels d'images<br/>sous les zones]
    P --> P2[Pages scannées :<br/>re-rastérisation + masquage pixels]
    P1 & P2 --> R[PDF reconstruit sans métadonnées,<br/>pièces jointes ni liens]
```

## Choix structurants

| Sujet | Choix | Raison |
|---|---|---|
| Déclenchement | Event Grid → **Service Bus** → Function | Retries, dead-letter, absorption des pics et rejeu. Un traitement dure de 10 à 120 s. |
| Calcul | **Functions Flex Consumption** (Python) | Paiement à l'usage, intégration VNet, identité managée. PyMuPDF s'installe par pip, sans conteneur. |
| PDF natifs | **PyMuPDF** : extraction des mots et `apply_redactions` | Le texte est réellement supprimé du PDF, pas seulement recouvert. |
| OCR | **Document Intelligence `prebuilt-read` sur toutes les pages** | Une page native peut contenir des images porteuses de données personnelles (logo d'un praticien ou d'un artisan, tampon, signature, carte collée). L'OCR est robuste aux scans de mauvaise qualité et renvoie un polygone et une confiance par mot. |
| Pages natives | Couche texte **+** texte OCR des images | Le texte natif reste la source principale, car il est exact et caviardable au caractère près. Seuls les mots OCR qui ne recouvrent aucun mot natif sont ajoutés, ce qui évite les doublons. |
| Détection | **Agent LLM** piloté par un prompt + **regex** + **AI Language** | Le LLM comprend le contexte (qui est une personne, quelle date est une date de naissance). Les filets déterministes sécurisent le rappel. |
| Contrôle | **Second agent LLM** (vérificateur) | Relit le résultat pseudonymisé et repère les oublis et les fragments résiduels. |
| Jetons | `HMAC-SHA256(clé, type + valeur normalisée)` | Une même personne reçoit le même jeton dans tous les documents. Le jeton n'est pas réversible sans la table. |
| Ré-identification | Valeur chiffrée **AES-256-GCM** dans Cosmos DB, avec le jeton en données associées | La table est séparée des documents et son accès est restreint et tracé (journal d'audit Key Vault). |
| Doute | **Quarantaine** plutôt que publication | Un faux négatif (fuite) coûte bien plus cher qu'un traitement manuel. |
| Prompts | Stockés dans un Blob versionné, rechargés à chaud (ETag) | Un expert métier peut les faire évoluer sans redéployer le code. |
| Résidence | OpenAI en déploiement **DataZoneStandard** (UE), autres services en France Central | Conformité RGPD. |

## Sécurité

- **Identités** : une identité managée affectée par l'utilisateur pour la Function, avec des
  rôles au plus juste (lecture seule sur `config`, contributeur sur les seuls containers utiles).
  Event Grid a sa propre identité (envoi vers la file uniquement).
- **Secrets** : uniquement les deux clés dans Key Vault (RBAC, purge protection, audit dans
  Log Analytics). Il n'y a aucune chaîne de connexion.
- **Réseau** (option `enable_private_networking`) : VNet, intégration VNet de la Function,
  private endpoints et zones DNS privées pour tous les services, accès publics fermés.
  Service Bus passe alors en Premium.
- **Journaux** : seuls des identifiants pseudonymes (`doc=<sha256 tronqué>`), des compteurs et
  des statuts sont journalisés, **jamais** de texte extrait ni de valeur détectée.
- **Données en transit chez les LLM** : Azure OpenAI ne conserve pas les prompts pour
  l'entraînement. Pour des données de santé, envisager la demande d'exemption de l'abuse
  monitoring (modified abuse monitoring) auprès de Microsoft.

## Limites connues (phase 1)

- Le masquage des pages scannées se fait au niveau du mot OCR : un mot partiellement
  concerné est masqué en entier.
- Sur une page native, le texte des images (logos, tampons, cartes collées) est lu par OCR et
  caviardé dans les pixels. En revanche, un logo **vectoriel** (dessiné en tracés, sans image) dont
  les tracés ne sont pas entièrement contenus dans la zone peut laisser des contours sous le
  masque gris. `FORCE_OCR=true` rastérise toutes les pages si l'on veut une garantie maximale.
- L'OCR ne lit que du **texte** : un visage sur une photo ou une signature manuscrite illisible
  ne sont pas détectés.
- Coût : chaque page est facturée par Document Intelligence (`prebuilt-read`), qu'elle soit
  native ou scannée. `OCR_NATIVE_PAGES=false` désactive l'OCR des pages natives, au prix de
  la perte de détection dans les images.
- Un jeton long dans une zone étroite (prénom court) s'affiche en petits caractères. Le
  fichier `.pseudonymise.txt` contient toujours les jetons complets.
- Un PDF protégé par mot de passe part en quarantaine.

## Évolutions

**Phase 2 : photos de smartphone.**
- Ajouter un prétraitement : rotation EXIF, conversion HEIC, correction de perspective,
  CLAHE et débruitage avec OpenCV.
- Faire passer ces fichiers par le même chemin que les pages scannées : OCR Document
  Intelligence puis rastérisation et masquage.
- Ajouter un repli vers un modèle vision quand la confiance OCR est faible.
- Le filtre Event Grid (`subject_ends_with = ".pdf"`) et `handler.py` sont les deux seuls
  points d'entrée à étendre.

**Autres pistes :**
- Une interface de revue de la quarantaine.
- Un jeu d'évaluation annoté, avec mesure automatique du rappel à chaque modification
  de prompt.
