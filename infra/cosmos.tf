# Table de correspondance jeton -> valeur chiffrée, et traces de traitement (sans donnée en clair).

resource "azurerm_cosmosdb_account" "main" {
  name                          = "cosmos-${local.base}-${random_string.suffix.result}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  offer_type                    = "Standard"
  kind                          = "GlobalDocumentDB"
  local_authentication_enabled  = false
  public_network_access_enabled = !local.private || length(var.allowed_ip_ranges) > 0
  ip_range_filter               = local.private ? toset(var.allowed_ip_ranges) : toset([])
  minimal_tls_version           = "Tls12"
  tags                          = local.tags

  capabilities {
    name = "EnableServerless"
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.main.location
    failover_priority = 0
  }

  backup {
    type = "Continuous"
    tier = "Continuous7Days"
  }
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "pseudonymisation"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "mappings" {
  name                = "mappings"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/id"]
}

resource "azurerm_cosmosdb_sql_container" "traces" {
  name                = "traces"
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/documentId"]
  default_ttl         = 31536000 # 1 an
}

resource "azurerm_cosmosdb_sql_role_assignment" "func" {
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_contributor}"
  principal_id        = azurerm_user_assigned_identity.func.principal_id
  scope               = azurerm_cosmosdb_account.main.id
}

# App images : traces (quarantine à la préparation) et lecture de la dernière trace (retraitement).
resource "azurerm_cosmosdb_sql_role_assignment" "func_images" {
  count               = var.enable_image_processing ? 1 : 0
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_contributor}"
  principal_id        = azurerm_user_assigned_identity.func_images[0].principal_id
  scope               = azurerm_cosmosdb_account.main.id
}

resource "azurerm_cosmosdb_sql_role_assignment" "reidentification" {
  for_each            = toset(var.reidentification_principal_ids)
  resource_group_name = azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_reader}"
  principal_id        = each.value
  scope               = azurerm_cosmosdb_account.main.id
}
