# ---------------------------------------------------------------------------- Contexte

variable "subscription_id" {
  description = "Abonnement Azure cible."
  type        = string
}

variable "project" {
  description = "Nom court du projet (minuscules/chiffres, 2 à 8 caractères), utilisé dans les noms de ressources."
  type        = string
  default     = "pseudo"

  validation {
    condition     = can(regex("^[a-z0-9]{2,8}$", var.project))
    error_message = "project : 2 à 8 caractères minuscules ou chiffres."
  }
}

variable "environment" {
  description = "Environnement (dev, rec, prod...)."
  type        = string
  default     = "dev"

  validation {
    condition     = can(regex("^[a-z0-9]{2,4}$", var.environment))
    error_message = "environment : 2 à 4 caractères minuscules ou chiffres."
  }
}

variable "location" {
  description = "Région principale des ressources."
  type        = string
  default     = "francecentral"
}

variable "tags" {
  description = "Tags appliqués à toutes les ressources."
  type        = map(string)
  default     = {}
}

variable "resource_group_name" {
  description = "Resource group de déploiement. null : rg-<project>-<environment>."
  type        = string
  default     = null
}

variable "create_resource_group" {
  description = "true : Terraform crée le resource group. false : il existe déjà (Terraform ne le crée ni ne le supprime)."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------- Storage d'origine

variable "source_storage_account_id" {
  description = <<-EOT
    ID du compte de stockage EXISTANT où arrivent les documents, éventuellement dans un autre resource
    group ou un autre abonnement (même tenant). Le déploiement y pose le déclencheur Event Grid et
    donne aux Functions l'accès en lecture au container source_container_name.
    null (défaut) : container d'entrée dans le compte « data » créé par ce déploiement.
  EOT
  type        = string
  default     = null

  validation {
    condition = var.source_storage_account_id == null || can(regex(
      "(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\\.Storage/storageAccounts/[^/]+$",
      var.source_storage_account_id,
    ))
    error_message = "source_storage_account_id : /subscriptions/<id>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<nom>."
  }
}

variable "source_container_name" {
  description = "Container d'arrivée des documents (dans le compte d'origine)."
  type        = string
  default     = "incoming"

  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9]|-[a-z0-9]){2,62}$", var.source_container_name))
    error_message = "source_container_name : 3 à 63 caractères, minuscules, chiffres et tirets isolés."
  }
}

variable "create_source_container" {
  description = "Compte d'origine existant : true pour que Terraform crée le container s'il n'existe pas encore."
  type        = bool
  default     = false
}

variable "source_system_topic_name" {
  description = <<-EOT
    Compte d'origine existant : nom d'un topic système Event Grid DÉJÀ rattaché à ce compte (Azure n'en
    autorise qu'un par compte), dans le resource group du compte. Il doit avoir une identité managée
    système. null : le déploiement crée le topic.
  EOT
  type        = string
  default     = null
}

# ---------------------------------------------------------------------------- Réseau

variable "enable_private_networking" {
  description = "Active VNet, private endpoints et désactive les accès publics (Service Bus passe en Premium)."
  type        = bool
  default     = false
}

variable "vnet_address_space" {
  description = "Plage d'adresses du VNet (si enable_private_networking)."
  type        = string
  default     = "10.40.0.0/24"
}

variable "allowed_ip_ranges" {
  description = "IP/CIDR publiques autorisées (poste de déploiement, runner CI) quand le réseau privé est activé."
  type        = list(string)
  default     = []
}

# ---------------------------------------------------------------------------- IA

variable "openai_location" {
  description = "Région du compte Azure OpenAI (disponibilité des modèles ; rester dans l'UE)."
  type        = string
  default     = "swedencentral"
}

variable "openai_model_name" {
  description = "Modèle Azure OpenAI utilisé par les agents (doit supporter les Structured Outputs)."
  type        = string
  default     = "gpt-5-mini"
}

variable "openai_model_version" {
  description = "Version du modèle."
  type        = string
  default     = "2025-08-07"
}

variable "openai_deployment_sku" {
  description = "Type de déploiement : DataZoneStandard (données traitées dans l'UE), Standard, GlobalStandard..."
  type        = string
  default     = "DataZoneStandard"
}

variable "openai_capacity" {
  description = "Capacité du déploiement en milliers de tokens/minute."
  type        = number
  default     = 100
}

variable "openai_api_version" {
  description = "Version d'API Azure OpenAI utilisée par le code."
  type        = string
  default     = "2025-04-01-preview"
}

variable "openai_reasoning_effort" {
  description = "Effort de raisonnement (modèles de raisonnement) : minimal, low, medium, high. Vide pour ne pas l'envoyer."
  type        = string
  default     = "low"
}

variable "pseudo_output_mode" {
  description = "Contenu du document publié : \"substitution\" (valeurs fictives réalistes, lisibles par un OCR) ou \"jetons\" ([NOM_3FA94C0B21]…)."
  type        = string
  default     = "substitution"

  validation {
    condition     = contains(["substitution", "jetons"], var.pseudo_output_mode)
    error_message = "pseudo_output_mode : \"substitution\" ou \"jetons\"."
  }
}

variable "handwriting_policy" {
  description = "Document contenant de l'écriture manuscrite (détectée par l'OCR) : \"quarantaine\" (revue humaine, aucun appel aux agents IA) ou \"traiter\"."
  type        = string
  default     = "quarantaine"

  validation {
    condition     = contains(["quarantaine", "traiter"], var.handwriting_policy)
    error_message = "handwriting_policy : \"quarantaine\" ou \"traiter\"."
  }
}

variable "enable_language_pii" {
  description = "Déploie Azure AI Language et l'utilise comme filet de sécurité PII."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------- Traitement

variable "function_max_instances" {
  description = "Nombre max d'instances Flex Consumption (limite aussi la charge sur Azure OpenAI)."
  type        = number
  default     = 10
}

variable "function_instance_memory_mb" {
  description = "Mémoire par instance (2048 ou 4096 recommandé pour les gros PDF scannés)."
  type        = number
  default     = 2048
}

variable "servicebus_max_delivery_count" {
  description = "Nombre de tentatives avant dead-letter."
  type        = number
  default     = 5
}

variable "delete_source_after_success" {
  description = "Supprime le document d'entrée après traitement (succès ou quarantaine). Donne alors aux Functions le droit d'écriture sur le container d'entrée (lecture seule sinon)."
  type        = bool
  default     = false
}

variable "incoming_retention_days" {
  description = "Suppression automatique des fichiers déposés dans incoming/ après N jours (compte « data » uniquement : rien n'est purgé sur un compte d'origine existant)."
  type        = number
  default     = 7
}

variable "work_retention_days" {
  description = "Purge des fichiers intermédiaires (work/) d'un traitement interrompu, après N jours."
  type        = number
  default     = 1
}

variable "quarantine_retention_days" {
  description = "Suppression automatique des fichiers en quarantaine après N jours."
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------- Images

variable "enable_mail_processing" {
  description = "Active le traitement des mails .eml (corps, en-têtes et pièces jointes pseudonymisés, un dossier par mail) : filtre Event Grid, fonction split_mail. Les pièces jointes images demandent aussi enable_image_processing."
  type        = bool
  default     = false
}

variable "enable_json_metadata" {
  description = "Traite les JSON de métadonnées de la GED (<nom du document>.json) : filtre Event Grid, file text-jobs. Le JSON pseudonymisé est publié à côté de son document (ou dans le dossier du mail)."
  type        = bool
  default     = true
}

variable "json_metadata_retention_days" {
  description = "Durée de conservation des JSON pseudonymisés en attente de leur document (work/json-metadata/), en jours. À aligner sur incoming_retention_days : un document retraité y retrouve son JSON."
  type        = number
  default     = 7
}

variable "mail_image_min_side" {
  description = "Mails : une image de moins de ce nombre de pixels sur un côté (logo, icône de signature) est retirée du mail, quel que soit son format. Les GIF sont toujours retirés."
  type        = number
  default     = 300

  validation {
    condition     = var.mail_image_min_side >= 0
    error_message = "mail_image_min_side doit être positif ou nul (0 = aucune image retirée pour sa taille)."
  }
}

variable "enable_image_processing" {
  description = "Active le traitement des images (photos, scans JPEG/PNG/HEIC/TIFF...) : app images, filtre Event Grid."
  type        = bool
  default     = false
}

variable "image_extensions" {
  description = "Extensions d'images transmises par Event Grid (si enable_image_processing)."
  type        = list(string)
  default     = [".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp"]

  validation {
    condition     = alltrue([for e in var.image_extensions : can(regex("^\\.[a-z0-9]+$", e))])
    error_message = "image_extensions : extensions en minuscules commençant par un point (ex. \".jpg\")."
  }
}

variable "image_function_max_instances" {
  description = "Nombre max d'instances de l'app images."
  type        = number
  default     = 5
}

variable "image_function_memory_mb" {
  description = "Mémoire par instance de l'app images (photos de 12 Mpx et plus : 4096 recommandé)."
  type        = number
  default     = 4096
}

variable "pipeline_settings" {
  description = "Paramètres additionnels du pipeline (app settings), ex. { MAX_PAGES = \"300\", FORCE_OCR = \"false\" }."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------- Sécurité

variable "key_vault_purge_protection" {
  description = "Protection contre la purge du Key Vault (recommandé en prod ; irréversible)."
  type        = bool
  default     = true
}

variable "grant_deployer_data_access" {
  description = "Donne à l'identité qui exécute Terraform l'accès aux données (dépôt des prompts, tests)."
  type        = bool
  default     = true
}

variable "reidentification_principal_ids" {
  description = "Object IDs Entra (groupe recommandé) autorisés à ré-identifier les jetons."
  type        = list(string)
  default     = []
}

variable "log_retention_days" {
  description = "Rétention Log Analytics."
  type        = number
  default     = 90
}

variable "deposit_timezone" {
  description = "Fuseau horaire (IANA) des dossiers de sortie year=/month=/day=/hour=, calculés d'après la date de dépose du document dans le container d'entrée."
  type        = string
  default     = "Europe/Paris"
}
