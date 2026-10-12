# Référentiel des types de document

<!--
  NOTE POUR LES MAINTENEURS (ce commentaire n'est envoyé ni à l'agent IA ni au code)

  Ce fichier est la SOURCE UNIQUE des types de document. Il est lu :
  - par l'agent de classification, qui y apprend à reconnaître chaque type ;
  - par le code, qui en déduit la liste des types autorisés et leur sous-dossier de sortie.

  Ajouter un type = ajouter une section, puis lancer scripts/publish-prompts.ps1
  (pris en compte en moins de 5 minutes, sans redéploiement du code).

  Convention OBLIGATOIRE pour chaque type :

    ## TYPE — Libellé
    - **Dossier** : nom-du-dossier

  - TYPE : MAJUSCULES, chiffres et _ uniquement (ex. AUDIOPROTHESE), suivi d'un tiret cadratin « — ».
  - Dossier (facultatif) : sous-dossier dans pseudonymised/ et quarantine/ ; minuscules, chiffres
    et « - » uniquement. Défaut : le type en minuscules (_ remplacé par -). Deux types ne peuvent
    pas partager un dossier.
  - NON_CLASSE est réservé : ne pas le déclarer. Les documents non reconnus vont dans le dossier
    UNCLASSIFIED_FOLDER (défaut : non-classe).
  - Les titres « ## » SANS tiret cadratin ne déclarent pas de type.
  - Toute erreur bloque le traitement et apparaît dans les journaux.

  Le texte analysé par l'agent est DÉJÀ PSEUDONYMISÉ : les noms, adresses, NIR... y sont remplacés
  par des jetons [NOM_XXXX]. Décrire les types par la spécialité, les actes, les codes et le
  vocabulaire, jamais par des noms de personnes.
-->

Le texte à classer est déjà pseudonymisé : les données personnelles y sont remplacées par des
jetons de la forme `[TYPE_XXXXXXXXXX]`. Appuie-toi sur la profession du praticien, la nature des
actes, les codes de facturation et le vocabulaire.

---

## DENTAIRE — Soins dentaires

- **Dossier** : dentaire
- **Indices** : chirurgien-dentiste, docteur en chirurgie dentaire, orthodontiste, centre
  dentaire ; codes CCAM commençant par `HB` (`HBMD…`, `HBLD…`, `HBQK…`) ; numéros de dents
  (11 à 48, notation FDI) ; couronne, bridge, inlay-core, implant, détartrage, obturation,
  composite, prothèse dentaire, panier « 100 % santé » dentaire, devis prothétique.
- **Ne pas confondre** : stomatologue ou chirurgien maxillo-facial facturant une consultation
  médicale sans acte dentaire (→ `NON_CLASSE` si aucun autre type ne convient).

## OPTIQUE — Optique et lunetterie

- **Dossier** : optique
- **Indices** : opticien, opticien-lunetier, magasin d'optique ; monture, verres (unifocaux,
  progressifs), lentilles de contact ; ordonnance d'ophtalmologiste ou d'orthoptiste ; correction
  OD/OG, sphère, cylindre, axe, addition, écart pupillaire ; codes LPP ; classe A / classe B ;
  renouvellement ou remplacement d'équipement.
- **Ne pas confondre** : consultation chez un ophtalmologiste sans délivrance d'équipement
  (acte médical, pas d'optique).

## KINESITHERAPIE — Masso-kinésithérapie

- **Dossier** : kinesitherapie
- **Indices** : masseur-kinésithérapeute, kinésithérapeute D.E. ; cotations `AMK`, `AMS`, `AMC` ;
  séances de rééducation (rachis, genou, épaule, périnéale…), bilan diagnostic kiné ; indemnité
  forfaitaire de déplacement (`IFD`) ; prescription médicale de séances ; rééducation posturale
  globale (RPG) facturée hors nomenclature par un kinésithérapeute.
- **Ne pas confondre** : ostéopathe ou chiropracteur non kinésithérapeute (→ `NON_CLASSE` tant
  qu'aucun type dédié n'existe).

## PSYCHOLOGIE — Psychologue et psychothérapie

- **Dossier** : psychologie
- **Indices** : psychologue, psychologue clinicien(ne), psychothérapeute, neuropsychologue ;
  numéro ADELI de psychologue ; dispositif « Mon soutien psy » ; séances ou entretiens de suivi
  psychologique, bilan psychologique ou neuropsychologique.
- **Ne pas confondre** : psychiatre (médecin : consultation médicale, → `NON_CLASSE` tant
  qu'aucun type dédié n'existe).

## CARTE_GRISE — Certificat d'immatriculation d'un véhicule

- **Dossier** : carte-grise
- **Indices** : « Certificat d'immatriculation », « carte grise » ; rubriques codifiées `A`, `B`,
  `C.1`, `C.3`, `D.1` (marque), `D.2`, `D.2.1` (CNIT), `D.3` (dénomination commerciale), `E`,
  `F.1`, `G`, `J` (catégorie `M1`…), `J.1` (genre `VP`, `CTTE`…), `K` (numéro de réception),
  `P.1` (cylindrée), `P.3` (carburant), `P.6` (puissance administrative), `S.1` (places), `V.7`
  (CO2), `X.1` (visite technique), `Y.1` à `Y.6` (taxes) ; numéro de formule ; mention
  « est le propriétaire du véhicule ».
- **Ne pas confondre** : facture de garage, constat amiable, contrat ou attestation d'assurance
  auto qui citent un véhicule sans en être le certificat d'immatriculation (→ `NON_CLASSE` tant
  qu'aucun type dédié n'existe).
