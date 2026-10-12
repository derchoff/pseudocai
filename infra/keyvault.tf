# Key Vault : clé HMAC (génération des jetons) et clé AES (chiffrement des valeurs d'origine).

resource "azurerm_key_vault" "main" {
  name                          = "kv-${local.compact}"
  location                      = local.location
  resource_group_name           = local.resource_group_name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = var.key_vault_purge_protection
  soft_delete_retention_days    = 90
  public_network_access_enabled = !local.private || length(var.allowed_ip_ranges) > 0
  tags                          = local.tags

  network_acls {
    bypass         = "AzureServices"
    default_action = local.private ? "Deny" : "Allow"
    ip_rules       = local.private ? var.allowed_ip_ranges : []
  }
}

resource "azurerm_role_assignment" "deployer_kv_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "func_kv_secrets" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "reidentification_kv" {
  for_each             = toset(var.reidentification_principal_ids)
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = each.value
}

# 256 bits aléatoires chacune. ATTENTION : leur valeur est présente dans l'état Terraform,
# qui doit donc être stocké dans un backend chiffré à accès restreint. En alternative, créer
# les secrets hors Terraform et supprimer ces ressources.
resource "random_bytes" "hmac_key" {
  length = 32
}

resource "random_bytes" "encryption_key" {
  length = 32
}

resource "azurerm_key_vault_secret" "hmac_key" {
  name         = "pseudo-hmac-key"
  value        = random_bytes.hmac_key.base64
  key_vault_id = azurerm_key_vault.main.id
  content_type = "base64; HMAC-SHA256 key"

  # Changer la clé HMAC change tous les jetons : la remplacer n'est jamais anodin.
  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [time_sleep.rbac_propagation]
}

resource "azurerm_key_vault_secret" "encryption_key" {
  name         = "pseudo-encryption-key"
  value        = random_bytes.encryption_key.base64
  key_vault_id = azurerm_key_vault.main.id
  content_type = "base64; AES-256-GCM key"

  lifecycle {
    ignore_changes = [value]
  }

  depends_on = [time_sleep.rbac_propagation]
}

# Audit des accès aux secrets (traçabilité des ré-identifications).
resource "azurerm_monitor_diagnostic_setting" "kv" {
  name                       = "diag-kv"
  target_resource_id         = azurerm_key_vault.main.id
  log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id

  enabled_log {
    category_group = "audit"
  }
}
