# Compte "data" : dépôt (incoming, sauf compte d'origine existant : voir source.tf), fichiers
# intermédiaires (work), sorties, quarantaine et prompts.
resource "azurerm_storage_account" "data" {
  name                            = "st${local.compact}data"
  location                        = local.location
  resource_group_name             = local.resource_group_name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  default_to_oauth_authentication = true
  public_network_access           = local.private && length(var.allowed_ip_ranges) == 0 ? "Disabled" : "Enabled"
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true # historique des prompts

    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }

  network_rules {
    default_action = local.private ? "Deny" : "Allow"
    bypass         = ["AzureServices"]
    ip_rules       = local.private ? var.allowed_ip_ranges : []
  }
}

resource "azurerm_storage_container" "data" {
  for_each              = local.containers
  name                  = each.value
  storage_account_id    = azurerm_storage_account.data.id
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "data" {
  storage_account_id = azurerm_storage_account.data.id

  # Uniquement si le container d'entrée est dans ce compte : un compte d'origine existant garde
  # ses propres règles.
  dynamic "rule" {
    for_each = local.source_external ? [] : [local.containers.incoming]
    content {
      name    = "purge-incoming"
      enabled = true
      filters {
        prefix_match = ["${rule.value}/"]
        blob_types   = ["blockBlob"]
      }
      actions {
        base_blob {
          delete_after_days_since_modification_greater_than = var.incoming_retention_days
        }
      }
    }
  }

  # Filet de sécurité : les fichiers intermédiaires sont supprimés en fin de traitement ; ceux
  # d'un traitement interrompu sont purgés ici.
  rule {
    name    = "purge-work"
    enabled = true
    filters {
      # Pas tout work/ : work/json-metadata/ a sa propre durée (règle suivante).
      prefix_match = ["${local.containers.work}/jobs/", "${local.containers.work}/mails/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.work_retention_days
      }
    }
  }

  # JSON de métadonnées pseudonymisés en attente de leur document (ou gardés pour un retraitement).
  rule {
    name    = "purge-json-metadata"
    enabled = true
    filters {
      prefix_match = ["${local.containers.work}/json-metadata/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.json_metadata_retention_days
      }
    }
  }

  rule {
    name    = "purge-quarantine"
    enabled = true
    filters {
      prefix_match = ["${local.containers.quarantine}/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.quarantine_retention_days
      }
    }
  }
}

# Compte "func" : stockage interne du runtime Functions et paquet de déploiement.
resource "azurerm_storage_account" "func" {
  name                            = "st${local.compact}func"
  location                        = local.location
  resource_group_name             = local.resource_group_name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
  default_to_oauth_authentication = true
  public_network_access           = local.private && length(var.allowed_ip_ranges) == 0 ? "Disabled" : "Enabled"
  tags                            = local.tags

  network_rules {
    default_action = local.private ? "Deny" : "Allow"
    bypass         = ["AzureServices"]
    ip_rules       = local.private ? var.allowed_ip_ranges : []
  }
}

resource "azurerm_storage_container" "deployments" {
  name                  = "deployments"
  storage_account_id    = azurerm_storage_account.func.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "deployments_images" {
  count                 = var.enable_image_processing ? 1 : 0
  name                  = "deployments-images"
  storage_account_id    = azurerm_storage_account.func.id
  container_access_type = "private"
}

# ------------------------------------------------------------------ RBAC stockage

# App principale : lecture et purge des fichiers intermédiaires, écriture des sorties et de la
# quarantaine, lecture des prompts. L'accès au container d'entrée est dans source.tf.
resource "azurerm_role_assignment" "func_data_containers" {
  for_each             = toset(["work", "pseudonymised", "quarantine"])
  scope                = azurerm_storage_container.data[each.key].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_config_reader" {
  scope                = azurerm_storage_container.data["config"].id
  role_definition_name = "Storage Blob Data Reader"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

# Runtime Functions (AzureWebJobsStorage + déploiement) via identité managée.
resource "azurerm_role_assignment" "func_storage_blob_owner" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Blob Data Owner"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_storage_queue" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Queue Data Contributor"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_storage_table" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Table Data Contributor"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

# App images : écriture des fichiers intermédiaires et de la quarantaine, nettoyage des sorties
# d'un traitement précédent (retraitement). L'accès au container d'entrée est dans source.tf.
resource "azurerm_role_assignment" "images_data_containers" {
  for_each             = var.enable_image_processing ? toset(["work", "pseudonymised", "quarantine"]) : toset([])
  scope                = azurerm_storage_container.data[each.key].id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.func_images[0].principal_id
}

# Runtime Functions de l'app images (même compte « func », container de déploiement distinct).
resource "azurerm_role_assignment" "images_storage_runtime" {
  for_each = var.enable_image_processing ? toset([
    "Storage Blob Data Owner",
    "Storage Queue Data Contributor",
    "Storage Table Data Contributor",
  ]) : toset([])
  scope                = azurerm_storage_account.func.id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.func_images[0].principal_id
}

# Déployeur : dépôt des prompts et des documents de test.
resource "azurerm_role_assignment" "deployer_data" {
  count                = var.grant_deployer_data_access ? 1 : 0
  scope                = azurerm_storage_account.data.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}
