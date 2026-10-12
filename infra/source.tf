# Storage d'origine : là où arrivent les documents et où le déclencheur Event Grid est posé.
# - source_storage_account_id = null (défaut) : container d'entrée du compte « data » créé ici ;
# - sinon : compte EXISTANT, éventuellement dans un autre resource group ou abonnement. Terraform
#   n'y crée que le topic Event Grid (sauf s'il est fourni), éventuellement le container, et les
#   droits de lecture des Functions. Le compte lui-même n'est jamais modifié.

locals {
  source_external = var.source_storage_account_id != null

  # [abonnement, resource group, nom] du compte existant.
  source_parts = local.source_external ? regex(
    "(?i)^/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/Microsoft\\.Storage/storageAccounts/([^/]+)$",
    var.source_storage_account_id,
  ) : []

  source_subscription_id     = local.source_external ? local.source_parts[0] : var.subscription_id
  source_resource_group_name = local.source_external ? local.source_parts[1] : local.resource_group_name
  source_account_name        = local.source_external ? local.source_parts[2] : azurerm_storage_account.data.name
  source_account_id          = local.source_external ? data.azurerm_storage_account.source[0].id : azurerm_storage_account.data.id
  source_location            = local.source_external ? data.azurerm_storage_account.source[0].location : local.location
  source_blob_endpoint       = local.source_external ? data.azurerm_storage_account.source[0].primary_blob_endpoint : azurerm_storage_account.data.primary_blob_endpoint
  source_container           = var.source_container_name

  source_container_id = (
    !local.source_external ? azurerm_storage_container.data["incoming"].id :
    var.create_source_container ? azurerm_storage_container.source[0].id :
    "${local.source_account_id}/blobServices/default/containers/${local.source_container}"
  )

  # Lecture seule, sauf si les Functions doivent supprimer le document après traitement.
  source_role = var.delete_source_after_success ? "Storage Blob Data Contributor" : "Storage Blob Data Reader"
}

data "azurerm_storage_account" "source" {
  count               = local.source_external ? 1 : 0
  provider            = azurerm.source
  name                = local.source_parts[2]
  resource_group_name = local.source_parts[1]
}

resource "azurerm_storage_container" "source" {
  count                 = local.source_external && var.create_source_container ? 1 : 0
  provider              = azurerm.source
  name                  = local.source_container
  storage_account_id    = data.azurerm_storage_account.source[0].id
  container_access_type = "private"
}

# ------------------------------------------------------------------ droits sur le container d'entrée

# App principale : le routeur vérifie et lit le fichier, la pseudonymisation le relit.
resource "azurerm_role_assignment" "func_source" {
  provider             = azurerm.source
  scope                = local.source_container_id
  role_definition_name = local.source_role
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

# App images : lit l'image d'origine.
resource "azurerm_role_assignment" "images_source" {
  count                = var.enable_image_processing ? 1 : 0
  provider             = azurerm.source
  scope                = local.source_container_id
  role_definition_name = local.source_role
  principal_id         = azurerm_user_assigned_identity.func_images[0].principal_id
}
