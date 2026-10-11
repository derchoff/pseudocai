variable "name" {
  description = "Nom de la Function App."
  type        = string
}

variable "plan_name" {
  description = "Nom du plan Flex Consumption (FC1, un plan par app)."
  type        = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "subscription_id" {
  description = "Abonnement ciblé par le contournement AzureWebJobsStorage (Azure CLI)."
  type        = string
}

variable "storage_account_name" {
  description = "Compte de stockage du runtime Functions (AzureWebJobsStorage)."
  type        = string
}

variable "storage_blob_endpoint" {
  description = "Point de terminaison blob de ce compte (se termine par /)."
  type        = string
}

variable "deployment_container_name" {
  description = "Container du paquet de déploiement (un par app)."
  type        = string
}

variable "identity_id" {
  description = "ID de l'identité managée affectée par l'utilisateur."
  type        = string
}

variable "identity_client_id" {
  description = "Client ID de cette identité (AZURE_CLIENT_ID)."
  type        = string
}

variable "app_insights_connection_string" {
  type      = string
  sensitive = true
}

variable "subnet_id" {
  description = "Sous-réseau d'intégration VNet (null sans réseau privé)."
  type        = string
  default     = null
}

variable "max_instances" {
  type = number
}

variable "memory_mb" {
  type = number
}

variable "app_settings" {
  description = "Paramètres d'application propres à l'app."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
