data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 4
  lower   = true
  upper   = false
  numeric = true
  special = false
}

locals {
  base    = "${var.project}-${var.environment}"
  compact = "${var.project}${var.environment}${random_string.suffix.result}"
  private = var.enable_private_networking

  tags = merge(
    {
      project     = var.project
      environment = var.environment
      managed_by  = "terraform"
      workload    = "pseudonymisation-pdf"
    },
    var.tags,
  )

  # Containers du compte « data » créé ici. incoming (point d'entrée) n'y figure que si l'entrée
  # n'est pas un compte existant (voir source.tf) ; work : fichiers intermédiaires des images.
  containers = merge(
    {
      work          = "work"
      pseudonymised = "pseudonymised"
      quarantine    = "quarantine"
      config        = "config"
    },
    local.source_external ? {} : { incoming = var.source_container_name },
  )

  # Rôles intégrés Cosmos DB (plan de données)
  cosmos_data_contributor = "00000000-0000-0000-0000-000000000002"
  cosmos_data_reader      = "00000000-0000-0000-0000-000000000001"
}

# Resource group : créé par Terraform (défaut) ou existant (create_resource_group = false).
resource "azurerm_resource_group" "main" {
  count    = var.create_resource_group ? 1 : 0
  name     = local.resource_group_name_wanted
  location = var.location
  tags     = local.tags
}

data "azurerm_resource_group" "existing" {
  count = var.create_resource_group ? 0 : 1
  name  = local.resource_group_name_wanted
}

moved {
  from = azurerm_resource_group.main
  to   = azurerm_resource_group.main[0]
}

locals {
  resource_group_name_wanted = coalesce(var.resource_group_name, "rg-${local.base}")
  # Référencer la ressource (ou la source de données) garantit l'ordre de création.
  resource_group_name = var.create_resource_group ? azurerm_resource_group.main[0].name : data.azurerm_resource_group.existing[0].name
  location            = var.location
}

# Identité managée de la Function : créée avant l'app pour que les rôles soient prêts au démarrage.
resource "azurerm_user_assigned_identity" "func" {
  name                = "id-${local.base}-func"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

# Identité de l'app images (étape de préparation), distincte : droits limités à son étape.
resource "azurerm_user_assigned_identity" "func_images" {
  count               = var.enable_image_processing ? 1 : 0
  name                = "id-${local.base}-func-img"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.base}"
  location            = local.location
  resource_group_name = local.resource_group_name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.base}"
  location            = local.location
  resource_group_name = local.resource_group_name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "other"
  tags                = local.tags
}

# Laisse à Entra ID le temps de propager les affectations de rôles avant leur utilisation.
resource "time_sleep" "rbac_propagation" {
  create_duration = "60s"

  depends_on = [
    azurerm_role_assignment.deployer_kv_secrets,
    azurerm_role_assignment.func_storage_blob_owner,
    azurerm_role_assignment.eventgrid_sb_sender,
    azurerm_role_assignment.func_source,
  ]
}
