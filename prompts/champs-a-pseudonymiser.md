# Référentiel des champs à pseudonymiser

<!--
  NOTE POUR LES MAINTENEURS (ce commentaire n'est envoyé ni aux agents IA ni au code)

  Ce fichier est la SOURCE UNIQUE des champs à pseudonymiser. Il est lu :
  - par les agents IA (détection et vérification), qui y apprennent à reconnaître chaque champ ;
  - par le code, qui en déduit la liste des types autorisés et leur normalisation.

  Ajouter un champ = ajouter une section, puis lancer scripts/publish-prompts.ps1
  (pris en compte en moins de 5 minutes, sans redéploiement du code).

  Convention OBLIGATOIRE pour chaque champ :

    ## TYPE — Libellé
    - **Normalisation** : texte | chiffres | alphanumerique | email | telephone | date
    - **Substitution** : nom | prenom | personne | date_naissance | nir | telephone | email | adresse | generique

  - TYPE : MAJUSCULES, chiffres et _ uniquement (ex. IBAN, NUMERO_PASSEPORT), suivi d'un tiret
    cadratin « — » ; c'est le nom qui apparaîtra dans les jetons ([IBAN_3FA94C0B21]).
  - Normalisation (facultative, défaut : texte) : forme canonique utilisée pour qu'une même
    valeur écrite différemment donne le même jeton :
      texte          accents, casse, espaces et ponctuation ignorés (noms, adresses...)
      chiffres       chiffres uniquement (numéros de dossier...)
      alphanumerique lettres et chiffres uniquement, en majuscules (NIR, IBAN, passeport...)
      email          espaces supprimés, minuscules
      telephone      chiffres, préfixe +33 / 0033 ramené à 0
      date           convertie en AAAA-MM-JJ quand le format est reconnu
  - Substitution (facultative, défaut : generique) : générateur de la VALEUR FICTIVE écrite dans
    le document publié en mode PSEUDO_OUTPUT_MODE=substitution. Les valeurs sont fabriquées par le
    code (pas par l'IA), au format de l'original, toujours les mêmes pour une même valeur :
      nom, prenom, personne (prénom + NOM), date_naissance (cohérente avec le NIR), nir (clé valide),
      telephone (tranches réservées à la fiction), email (@example.com), adresse (vrai code postal
      et ville, fausse voie), generique (même forme : lettres -> lettres, chiffres -> chiffres)
  - Les titres « ## » SANS tiret cadratin (ex. « Règles transverses ») ne déclarent pas de champ.
  - Toute erreur (type mal écrit, doublon, normalisation inconnue) bloque le traitement et
    apparaît dans les journaux : un champ n'est jamais ignoré silencieusement.
-->

Principe général : **en cas de doute, on pseudonymise**. Un faux positif (masquage en trop)
est acceptable ; un faux négatif (donnée personnelle laissée en clair) ne l'est pas.

Le texte fourni provient d'une extraction PDF ou d'un OCR (scan, photo) : il peut contenir des erreurs
de reconnaissance (`O`↔`0`, `l`/`I`↔`1`, `S`↔`5`, `B`↔`8`), des espaces parasites au milieu
des mots ou des nombres, des retours à la ligne arbitraires et des mots collés à leur libellé
(`Nom:DUPONT`).

---

## NOM — Nom de famille d'une personne physique

- **Normalisation** : texte
- **Substitution** : nom
- **Définition** : nom de famille, nom de naissance, nom d'usage, nom marital de toute
  personne physique citée dans le document (titulaire, patient, assuré, conjoint, enfant,
  signataire, médecin, conseiller, témoin, etc.).
- **Indices** :
  - libellés : `Nom`, `Nom de famille`, `Nom de naissance`, `Nom d'usage`, `Nom marital`,
    `Époux/Épouse`, `né(e)` suivi d'un nom (ex. `Mme Durand née Martin` → 2 noms),
    `Soussigné(e)`, `Signataire` ;
  - civilités et titres : `M.`, `Mr`, `Mme`, `Mlle`, `Monsieur`, `Madame`, `Dr`, `Docteur`,
    `Pr`, `Professeur`, `Me`, `Maître`. Ils annoncent un nom mais **ne font jamais partie de la
    valeur** : `Dr DUPONT` → NOM `DUPONT` seulement ; `Docteur` seul n'est rien à signaler ;
  - écriture en MAJUSCULES à côté d'un prénom en casse normale (`Jean DUPONT`) ;
  - blocs d'adresse de destinataire, signatures en bas de page, champs « Fait par » ;
  - rubrique `C.1` d'une carte grise (titulaire) : nom PUIS prénom, tous deux en MAJUSCULES
    (`LEMAIRE SOPHIE` → NOM `LEMAIRE`, PRENOM `SOPHIE`), parfois sur deux lignes ;
  - rubrique `C.4.1` d'une carte grise : nombre de titulaires suivi du nom du cotitulaire
    (`C.4.1 2 DELAROCHE` → NOM `DELAROCHE` ; le chiffre `2` n'est pas à marquer) ;
  - actes de naissance, de décès et actes notariés : l'enfant ou le défunt, ses parents
    (`fils de`, `fille de`), son conjoint (`époux de`, `épouse de`, `veuf de`, `veuve de`), le
    déclarant, les témoins, l'officier de l'état civil, le notaire (`Maître`, `Me`) et les
    héritiers sont tous des personnes physiques : signaler leurs noms et prénoms.
- **Inclure** : noms composés (`Dupont-Moretti`, `de La Fontaine`) en entier, particules
  comprises.
- **Ne pas inclure** : noms de sociétés, d'établissements, de marques, de produits, de
  médicaments ; noms de villes ; noms de voies quand ils font partie d'une adresse (ils
  relèvent alors du type ADRESSE) ; sur une carte grise, marque et modèle du véhicule
  (rubriques `D.1`, `D.2`, `D.3`, ex. `PEUGEOT`, `308`), même écrits en MAJUSCULES comme un nom ;
  civilités et titres (`Dr`, `Docteur`…) ; mots du vocabulaire du document, même en MAJUSCULES
  ou en début de ligne (`DEVIS`, `Facture`, voir « Mots à ne jamais pseudonymiser »).

## PRENOM — Prénom d'une personne physique

- **Normalisation** : texte
- **Substitution** : prenom
- **Définition** : prénom(s) usuel(s) ou d'état civil, y compris 2e/3e prénoms.
- **Indices** : libellés `Prénom`, `Prénoms`, `Prénom usuel` ; mot en casse normale accolé
  à un NOM en majuscules ; formule de politesse nominative (`Cher Jean,`).
- **Inclure** : prénoms composés (`Jean-Pierre`) en entier ; **initiale de prénom**
  accompagnant un nom (`J. DUPONT` → `J.` est un PRENOM) ; liste de prénoms d'état civil
  séparés par des virgules (`Jean, Pierre, Marie DUPONT`) : un PRENOM par prénom, sans la
  virgule.
- **Ne pas inclure** : prénoms entrant dans un nom de voie (`rue Victor Hugo` → ADRESSE),
  d'établissement (`Hôpital Georges Pompidou`) ou de société ; **mots ordinaires** du document
  (`devis`, `Devis`, `DEVIS`, `facture`, `note`…), même en casse de prénom ou en MAJUSCULES :
  un prénom désigne une personne, jamais un document ou un acte ; civilités et titres (`Dr`,
  `Docteur`, `Mme`…).

## DATE_NAISSANCE — Date de naissance

- **Normalisation** : date
- **Substitution** : date_naissance
- **Définition** : date de naissance d'une personne physique, quel que soit son format.
- **Formats** : `12/03/1985`, `12-03-85`, `12.03.1985`, `12 mars 1985`, `1985-03-12`,
  `12/03/85`, `né le 12 mars`, `03/1985` (mois/année seul).
- **Dates en toutes lettres** (actes de naissance, actes de décès, actes notariés) :
  `vingt-trois mars mil neuf cent cinquante-deux`, `premier janvier deux mille`,
  `dix-neuf février mille neuf cent quatre-vingt-dix-sept`, ou mélangées
  (`23 mars mil neuf cent cinquante-deux`). Ce sont des dates de naissance comme les autres :
  - signaler la date **telle qu'elle est écrite, en lettres** : ne JAMAIS la convertir en
    chiffres (`vingt-trois mars mil neuf cent cinquante-deux`, pas `23/03/1952`) ;
  - la valeur va du jour à l'année inclus : sans `le`, sans l'heure (`à dix heures trente`),
    sans le lieu (`à Lyon`) ;
  - une date en lettres est longue et souvent **coupée par un retour à la ligne** : produire un
    fragment par ligne avec le même `group` (`vingt-trois mars mil neuf` sur une ligne,
    `cent cinquante-deux` sur la suivante) ; un mot coupé par un tiret en fin de ligne
    (`cinquante-` / `deux`) donne aussi un fragment par ligne.
- **Indices** : `Date de naissance`, `Né(e) le`, `Née le`, `DDN`, `D.N.`, `Naissance`,
  `Date of birth`, `DOB`, position dans un bloc d'identité juste après nom et prénom.
  Dans un **acte de naissance**, la date qui ouvre l'acte (`Le vingt-trois mars mil neuf cent
  cinquante-deux, à dix heures, est né …`) est la date de naissance. Dans un **acte de décès**,
  la date qui suit `né le`, `née le` ou `né(e) à … le` est la date de naissance du défunt ;
  celles des parents, de l'époux ou du déclarant (`né le …` à côté de leur nom) aussi.
- **Ne pas inclure** : date du document, date d'émission ou d'expiration, date de soins,
  date de signature, dates d'échéance ; dans un acte d'état civil ou notarié : date de
  l'acte (`Dressé le …`, `Délivré le …`), date du décès (`est décédé(e) le …`), date de la
  déclaration, dates des mentions marginales (mariage, divorce). Une date isolée sans aucun indice de naissance
  n'est à retenir que si elle se trouve dans un bloc d'identité d'une personne (dans ce cas
  `confidence` ≤ 0.6).
- **Lieu de naissance** : non demandé (ne pas le marquer).

## NIR — Numéro de sécurité sociale (NIR / numéro INSEE)

- **Normalisation** : alphanumerique
- **Substitution** : nir
- **Définition** : numéro d'inscription au répertoire des personnes physiques, figurant
  notamment sur la carte Vitale et les attestations de droits.
- **Structure** : 13 caractères + clé de 2 chiffres = 15 caractères, souvent espacés :
  `S AA MM DD CCC OOO KK`
  - `S` : 1 ou 2 (3, 7 ou 8 pour les numéros provisoires/NIA) ;
  - `AA` : année de naissance ; `MM` : mois (01-12, ou 20-42 / 50-99 cas particuliers) ;
  - `DD` : département (2 chiffres, ou `2A` / `2B` pour la Corse ; 3 chiffres pour l'outre-mer
    avec une commune sur 2 chiffres) ;
  - `CCC` : commune ; `OOO` : n° d'ordre ; `KK` : clé (01-97).
- **Exemples de formes** : `1 85 03 75 123 456 78`, `185037512345678`, `2 90 12 2A 004 012 34`,
  `1850375123456 78`.
- **Indices** : `N° SS`, `N° Sécu`, `N° de sécurité sociale`, `N° INSEE`, `NIR`,
  `Immatriculation`, `N° d'assuré`, `Carte Vitale`.
- **Inclure** la clé si elle est présente ; un NIR à 13 caractères sans clé est aussi un NIR.
- **Ne pas confondre** avec : IBAN, SIRET/SIREN, numéro de dossier, numéro de facture,
  numéro de téléphone, numéro ADELI/RPPS ; sur une carte grise, `Immatriculation` désigne la
  plaque du véhicule (type IMMATRICULATION), pas le NIR.

## EMAIL — Adresse électronique

- **Normalisation** : email
- **Substitution** : email
- **Définition** : toute adresse e-mail (personnelle ou professionnelle nominative).
- **Formes** : `prenom.nom@domaine.fr`, avec éventuelles erreurs OCR : espaces autour de `@`
  ou du `.`, `(at)`, `[at]`, ` arobase `, `@` lu comme `©` ou `&`.
- **Inclure** l'adresse complète (partie locale + domaine).
- **Ne pas inclure** : URL de site web sans `@` (`www.exemple.fr`).

## TELEPHONE — Numéro de téléphone ou de fax

- **Normalisation** : telephone
- **Substitution** : telephone
- **Définition** : numéro de téléphone fixe, mobile ou fax d'une personne (y compris
  numéros professionnels directs).
- **Formes** : `06 12 34 56 78`, `06.12.34.56.78`, `0612345678`, `+33 6 12 34 56 78`,
  `+33 (0)6 12 34 56 78`, `0033 612345678`, numéros étrangers `+32 …`, `+41 …`.
- **Indices** : `Tél`, `Tel.`, `Téléphone`, `Mobile`, `Portable`, `Port.`, `Fixe`, `Fax`,
  `Domicile`, `Bureau`, `☎`, `📞`.
- **Ne pas inclure** : numéros de standard génériques d'une organisation **uniquement s'ils
  sont clairement non nominatifs** (ex. numéro court `3646`) ; NIR, IBAN, SIRET, montants,
  références de dossier.

## ADRESSE — Adresse postale

- **Normalisation** : texte
- **Substitution** : adresse
- **Définition** : adresse postale complète ou partielle. **Politique retenue : toutes les
  adresses postales sont pseudonymisées**, qu'elles concernent une personne physique ou une
  organisation (modifiez cette règle ici si vous souhaitez conserver les adresses des
  organisations).
- **Composants à inclure** : numéro et indice (`12 bis`), type et nom de voie
  (`rue`, `avenue`, `bd`, `chemin`, `allée`, `impasse`, `lieu-dit`…), compléments
  (`Bât. B`, `Apt 12`, `Résidence Les Pins`, `Escalier 3`, `BP 123`, `CS 40001`), code postal
  (5 chiffres, ex. `75001`, `2A004`), ville, `CEDEX`, pays si accolé à l'adresse.
- **Adresse sur plusieurs lignes** : retourner **un fragment par ligne**, tous avec le même
  identifiant `group` (ex. `"a1"`).
- **Ne pas inclure** : ville citée seule hors adresse (`Fait à Paris`), libellés (`Adresse :`).

## IMMATRICULATION — Numéro d'immatriculation d'un véhicule (plaque)

- **Normalisation** : alphanumerique
- **Substitution** : generique
- **Définition** : numéro inscrit sur la plaque d'un véhicule, qui permet de remonter à son
  titulaire.
- **Formes** : format actuel `AB-123-CD` (aussi `AB 123 CD`, `AB123CD`) ; ancien format
  `1234 AB 75`, `123 ABC 2A` ; plaques étrangères quand elles sont désignées comme telles.
- **Indices** : rubrique `A` d'une carte grise (certificat d'immatriculation), `Immatriculation`,
  `N° d'immatriculation`, `Plaque`, `Véhicule immatriculé`, constats amiables, PV, factures de
  garage, contrats d'assurance auto ; sur une carte grise, le numéro est **répété sans libellé**
  dans le coupon détachable et, sans tirets (`AB123CD`), dans la bande de lecture automatique
  (voir les règles transverses).
- **Ne pas confondre** : sur une carte grise, « immatriculation » désigne la plaque, pas le NIR ;
  numéro de formule de la carte grise (type NUMERO_FORMULE) ; numéro d'identification du
  véhicule (type NUMERO_VIN) ; code de réception `e2*2007/46*…` ; CNIT (rubrique `D.2.1`).

## NUMERO_VIN — Numéro d'identification du véhicule (VIN, numéro de châssis)

- **Normalisation** : alphanumerique
- **Substitution** : generique
- **Définition** : identifiant unique du véhicule, gravé sur le châssis.
- **Formes** : 17 caractères, lettres majuscules et chiffres, sans `I`, `O` ni `Q`
  (ex. `VF3LBHNZ6KS214587`), parfois avec des espaces parasites dus à l'OCR.
- **Indices** : rubrique `E` d'une carte grise, `VIN`, `N° de série`, `Numéro de châssis`,
  `N° d'identification du véhicule`, `N° de série du type` ; répété sans libellé dans le coupon
  détachable et dans la bande de lecture automatique d'une carte grise.
- **Ne pas confondre** : type, variante, version (rubrique `D.2`), CNIT (rubrique `D.2.1`),
  numéro de réception (rubrique `K`).

## NUMERO_FORMULE — Numéro de formule d'une carte grise

- **Normalisation** : alphanumerique
- **Substitution** : generique
- **Définition** : numéro du document carte grise lui-même, demandé notamment pour une
  cession ou une déclaration de perte.
- **Formes** :
  - format actuel : année d'émission sur 4 chiffres + 2 lettres + 5 chiffres (`2019AB45218`) ;
  - formats anciens : année sur 2 chiffres + 2 lettres + 6 chiffres (`98TN036623`), ou autres
    combinaisons de chiffres et de lettres de 10 à 11 caractères.
- **Indices** : `N° de formule`, `Numéro de formule`, `Formule n°`, en-tête ou bas d'une carte
  grise ; répété sans libellé dans le coupon détachable (souvent sous le numéro
  d'immatriculation et la date) et dans la 2e ligne de la bande de lecture automatique.
- **Ne pas confondre** : numéro d'immatriculation (type IMMATRICULATION), numéro de facture ou de
  dossier.

---

## Mots à ne jamais pseudonymiser

<!--
  NOTE POUR LES MAINTENEURS : cette section est AUSSI lue par le code (pseudo/exceptions.py), qui
  l'applique après toutes les détections (agent IA, règles, Azure AI Language, propagation) :
  - une zone qui ne contient QUE des mots de la liste n'est pas pseudonymisée ;
  - un mot de la liste au début ou à la fin d'une zone en est retiré (`Dr DUPONT` → `DUPONT`).
  Casse et accents ignorés ; valeurs séparées par des virgules ; un mot = une valeur (pas de
  phrase). Ne jamais y mettre un vrai nom de famille : il ne serait plus pseudonymisé nulle part.
-->

Ces mots ne sont jamais des données personnelles, quelle que soit leur casse (`devis`, `Devis`,
`DEVIS`) ou leur position (début de ligne, titre en MAJUSCULES) : ne jamais les signaler, ni seuls
ni comme partie d'une valeur.

- **Civilités et titres** : M., Mr, Mme, Mlle, Monsieur, Madame, Mademoiselle, Messieurs,
  Mesdames, Dr, Dr., Docteur, Docteurs, Pr, Pr., Professeur, Me, Maître
- **Mots courants** : devis, facture, factures, avoir, note, honoraires, reçu, recu, ordonnance,
  prescription, attestation, certificat, bordereau, décompte, decompte, relevé, releve, courrier,
  objet, référence, reference, total, montant, patient, patiente, assuré, assure, adhérent,
  adherent, bénéficiaire, beneficiaire, praticien, signature, cachet
- **Comptes et services** : Administrateur, Gestion

## Champs JSON à ne jamais modifier

<!--
  NOTE POUR LES MAINTENEURS : section lue par le code (pseudo/json_metadata.py) pour les fichiers
  JSON de métadonnées de la GED (<nom du document>.json). Les valeurs de ces champs sont recopiées
  telles quelles, sans passer par les agents IA ; toutes les AUTRES valeurs texte du JSON sont
  pseudonymisées. Un chemin par puce, entre accents graves :
  - les clés sont séparées par des points : `GED_metadata.metadatas.Statut` ;
  - `[]` désigne tous les éléments d'un tableau : `GED_metadata.ancestors[].id` ;
  - `*` en dernière position désigne tout ce qui est en dessous : `DOCUMENT.*` ;
  - `*` dans un nom de clé remplace une suite de caractères : `Gestionnaire dédié *`.
  Casse et accents des clés comptent : recopier les noms exacts du JSON.
-->

Les fichiers JSON de métadonnées de la GED reprennent ces champs à l'identique : ils ne
contiennent pas de données personnelles.

- `DOCUMENT.*`
- `GED_metadata.id`
- `GED_metadata.label`
- `GED_metadata.size`
- `GED_metadata.mimetype`
- `GED_metadata.created`
- `GED_metadata.docType`
- `GED_metadata.modified`
- `GED_metadata.ancestors[].id`
- `GED_metadata.ancestors[].type`
- `GED_metadata.metadatas.Statut`
- `GED_metadata.metadatas.Date de statut`
- `GED_metadata.metadatas.Nature`
- `GED_metadata.metadatas.Domaine`
- `GED_metadata.metadatas.Famille`
- `GED_metadata.metadatas.Priorité`
- `GED_metadata.metadatas.A externaliser`
- `GED_metadata.metadatas.Souscripteur`
- `GED_metadata.metadatas.Apporteur`
- `GED_metadata.metadatas.Porteur de risques`
- `GED_metadata.metadatas.Produit`
- `GED_metadata.metadatas.Num interne contrat`
- `GED_metadata.metadatas.ANI`
- `GED_metadata.metadatas.Date d'arrivée`
- `GED_metadata.metadatas.Date de qualification`
- `GED_metadata.metadatas.Identifiant lot`
- `GED_metadata.metadatas.Identifiant pli`
- `GED_metadata.metadatas.Sens`
- `GED_metadata.metadatas.Distribution`
- `GED_metadata.metadatas.Emetteur, Destinataire`
- `GED_metadata.metadatas.Formation`
- `GED_metadata.metadatas.Contrôle`
- `GED_metadata.metadatas.Reprise`
- `cafeteria_decompte.*`

## Libellés de service

<!--
  NOTE POUR LES MAINTENEURS : section lue par le code (pseudo/json_metadata.py). Dans les champs
  JSON désignés par « Champs » (même notation que la section précédente), une valeur qui COMMENCE
  par un des mots de « Commencent par » (mot entier, casse et accents ignorés) est un libellé de
  service, jamais une personne : elle est recopiée telle quelle. Une autre valeur de ces champs
  (un nom de gestionnaire) reste pseudonymisée. Hors de ces champs, la règle ne s'applique pas :
  ailleurs, « Administrateur » ou « Gestion » peuvent être suivis d'un vrai nom.
-->

- **Champs** : `GED_metadata.metadatas.Gestionnaire dédié *`
- **Commencent par** : Administrateur, Gestion

## Organisations à ne jamais pseudonymiser

<!--
  NOTE POUR LES MAINTENEURS : cette section est AUSSI lue par le code (pseudo/exceptions.py), qui
  l'applique après toutes les détections (agent IA, règles, Azure AI Language), dans tous les
  documents et dans les en-têtes des mails. Garder les trois puces et leur nom exact ; valeurs
  séparées par des virgules. Le titre n'a pas de tiret cadratin : ce n'est pas un champ.
-->

- **Noms** : SG Santé, SGSanté, Howden, Howden France, Ageo
- **Domaines** : ageo.fr, sgsante.fr, howden-france.com, howdengroup.com
- **Adresses génériques** : contact, info, infos, accueil, service, services, client, clients,
  gestion, sinistre, sinistres, prestation, prestations, adhesion, adhesions, devis, facture,
  factures, facturation, comptabilite, courrier, secretariat, support, noreply, no, reply, ne, pas,
  repondre, sante, relation, relations

Ces organisations sont nos partenaires : leur nom n'est pas une donnée personnelle et ne doit pas
être signalé. Pour une adresse e-mail d'un de ces domaines :
- adresse **générique** d'un service (`contact@sgsante.fr`, `sinistres@howden-france.com`) : ne
  pas la signaler ;
- adresse **nominative** d'un salarié (`marie.durand@sgsante.fr`) : la signaler comme EMAIL, et
  signaler aussi son nom et son prénom s'ils apparaissent (c'est une personne physique).

---

## Règles transverses

1. Une valeur citée plusieurs fois doit être signalée **à chaque occurrence**.
2. Ne jamais inclure le libellé dans la valeur (`Nom : DUPONT` → `DUPONT`), sauf si le libellé
   est collé à la valeur sans séparateur et indissociable (`Nom:DUPONT` → `DUPONT`).
3. Recopier la valeur **exactement** telle qu'elle apparaît dans la ligne (mêmes erreurs OCR,
   même casse, mêmes espaces). Ne jamais corriger ni normaliser.
4. Les jetons de pseudonymisation déjà présents de la forme `[TYPE_XXXXXXXX]` ne sont jamais
   des données personnelles.
5. **Répétitions sans libellé** : certains documents répètent leurs données dans une partie
   détachable ou récapitulative, sans libellé (coupon détachable d'une carte grise, talon
   d'un chèque, souche d'un formulaire). Une valeur déjà reconnue ailleurs dans le document
   doit aussi être signalée là, même isolée sur sa ligne.
6. **Bandes de lecture automatique** (lignes de caractères majuscules et de chiffres avec des
   chevrons `<`, en bas d'une carte grise, d'une carte d'identité ou d'un passeport) : les
   données y sont **collées** les unes aux autres, sans espace ni tiret. Signaler **chaque
   valeur incluse**, avec son type, en recopiant **exactement la portion de la chaîne** qui la
   contient. Exemples sur une carte grise :
   - `CRFRAAB123CD0VF7X18E00018E8472…` → IMMATRICULATION `AB123CD`, NUMERO_VIN
     `VF7X18E00018E8472` ;
   - `CI<<MERSSAIDESS<<<SR220TD<<<<<<98TN036623<38` → NUMERO_FORMULE `98TN036623`
     (`MERSSAIDESS` et `SR220TD` sont la marque et le modèle : à ne pas marquer).
   Sur une carte d'identité ou un passeport, les noms et prénoms y sont séparés par `<<` et la
   date de naissance y figure au format `AAMMJJ` : les signaler aussi.
7. **Texte suivi** (actes d'état civil, actes notariés, courriers) : les lignes sont coupées
   au milieu des phrases. Une valeur coupée par un retour à la ligne (date en lettres, nom
   composé, adresse) donne **un fragment par ligne avec le même `group`**, chacun recopié
   exactement depuis sa ligne ; un mot coupé par un tiret en fin de ligne (`Marie-Thé-` puis
   `rèse`) aussi. Ne jamais recoller les morceaux dans une seule valeur.
