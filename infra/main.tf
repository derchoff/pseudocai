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

  containers = {
    incoming      = "incoming"
    pseudonymised = "pseudonymised"
    quarantine    = "quarantine"
    config        = "config"
  }

  # Rôles intégrés Cosmos DB (plan de données)
  cosmos_data_contributor = "00000000-0000-0000-0000-000000000002"
  cosmos_data_reader      = "00000000-0000-0000-0000-000000000001"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.base}"
  location = var.location
  tags     = local.tags
}

# Identité managée de la Function : créée avant l'app pour que les rôles soient prêts au démarrage.
resource "azurerm_user_assigned_identity" "func" {
  name                = "id-${local.base}-func"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
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
  ]
}
