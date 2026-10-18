# Table de correspondance jeton -> valeur chiffrée, et traces de traitement (sans donnée en clair).

resource "azurerm_cosmosdb_account" "main" {
  name                          = "cosmos-${local.base}-${random_string.suffix.result}"
  location                      = local.location
  resource_group_name           = local.resource_group_name
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
    location          = local.location
    failover_priority = 0
  }

  backup {
    type = "Continuous"
    tier = "Continuous7Days"
  }
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "pseudonymisation"
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "mappings" {
  name                = "mappings"
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/id"]
}

resource "azurerm_cosmosdb_sql_container" "traces" {
  name                = "traces"
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/documentId"]
  default_ttl         = 31536000 # 1 an
}

# Une fiche par zone remplacée dans un document publié : document, page, position, type, jeton,
# valeur fictive. Aucune valeur d'origine (elle reste chiffrée dans « mappings », via le jeton).
# Sert à la ré-identification « tel document, telle valeur fictive ». Pas d'expiration : les
# fiches vivent aussi longtemps que les documents publiés (remplacées à chaque retraitement).
resource "azurerm_cosmosdb_sql_container" "occurrences" {
  name                = "occurrences"
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/documentId"]
}

# Une fiche par traitement (document, pièce de mail, JSON de métadonnées) : période de dépose,
# catégorie et type MIME, statut, pages, tours de vérification, consommation OCR et IA. Aucune
# donnée personnelle. Partition par jour de dépose ; conservation illimitée (pas de TTL).
# Lu par scripts/statistiques.py (voir docs/statistiques.md).
resource "azurerm_cosmosdb_sql_container" "statistiques" {
  name                = "statistiques"
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  database_name       = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths = ["/jour"]
}

resource "azurerm_cosmosdb_sql_role_assignment" "func" {
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_contributor}"
  principal_id        = azurerm_user_assigned_identity.func.principal_id
  scope               = azurerm_cosmosdb_account.main.id
}

# App images : traces (quarantine à la préparation) et lecture de la dernière trace (retraitement).
resource "azurerm_cosmosdb_sql_role_assignment" "func_images" {
  count               = var.enable_image_processing ? 1 : 0
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_contributor}"
  principal_id        = azurerm_user_assigned_identity.func_images[0].principal_id
  scope               = azurerm_cosmosdb_account.main.id
}

resource "azurerm_cosmosdb_sql_role_assignment" "reidentification" {
  for_each            = toset(var.reidentification_principal_ids)
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_reader}"
  principal_id        = each.value
  scope               = azurerm_cosmosdb_account.main.id
}

# Lecteurs des statistiques (scripts/statistiques.py) : lecture du SEUL container « statistiques »,
# sans accès aux correspondances ni aux occurrences.
resource "azurerm_cosmosdb_sql_role_assignment" "statistics_readers" {
  for_each            = toset(var.statistics_reader_principal_ids)
  resource_group_name = local.resource_group_name
  account_name        = azurerm_cosmosdb_account.main.name
  role_definition_id  = "${azurerm_cosmosdb_account.main.id}/sqlRoleDefinitions/${local.cosmos_data_reader}"
  principal_id        = each.value
  scope               = "${azurerm_cosmosdb_account.main.id}/dbs/${azurerm_cosmosdb_sql_database.main.name}/colls/${azurerm_cosmosdb_sql_container.statistiques.name}"
}

# Traçabilité RGPD : chaque requête plan de données (lecture/écriture, y compris les accès aux
# mappings et occurrences, donc toute ré-identification) est journalisée dans Log Analytics.
# Les entrées ne contiennent que des identifiants (jetons, documentId) : aucune valeur d'origine.
resource "azurerm_monitor_diagnostic_setting" "cosmos" {
  name                       = "diag-cosmos"
  target_resource_id         = azurerm_cosmosdb_account.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category = "DataPlaneRequests"
  }
  enabled_log {
    category = "ControlPlaneRequests"
  }
}
