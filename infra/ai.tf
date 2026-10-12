# Azure OpenAI (agents), Document Intelligence (OCR des pages scannées), Language (filet PII).
# Authentification Entra ID uniquement (clés désactivées) : custom_subdomain_name obligatoire.

locals {
  cognitive_network_acls = local.private ? [1] : []
}

resource "azurerm_cognitive_account" "openai" {
  name                          = "oai-${local.base}-${random_string.suffix.result}"
  location                      = var.openai_location
  resource_group_name           = local.resource_group_name
  kind                          = "OpenAI"
  sku_name                      = "S0"
  custom_subdomain_name         = "oai-${local.base}-${random_string.suffix.result}"
  local_auth_enabled            = false
  public_network_access_enabled = !local.private || length(var.allowed_ip_ranges) > 0
  tags                          = local.tags

  dynamic "network_acls" {
    for_each = local.cognitive_network_acls
    content {
      default_action = "Deny"
      ip_rules       = var.allowed_ip_ranges
      bypass         = "AzureServices"
    }
  }
}

resource "azurerm_cognitive_deployment" "llm" {
  name                 = "pseudo-llm"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = var.openai_model_name
    version = var.openai_model_version
  }

  sku {
    name     = var.openai_deployment_sku
    capacity = var.openai_capacity
  }
}

resource "azurerm_cognitive_account" "docintel" {
  name                          = "di-${local.base}-${random_string.suffix.result}"
  location                      = local.location
  resource_group_name           = local.resource_group_name
  kind                          = "FormRecognizer"
  sku_name                      = "S0"
  custom_subdomain_name         = "di-${local.base}-${random_string.suffix.result}"
  local_auth_enabled            = false
  public_network_access_enabled = !local.private || length(var.allowed_ip_ranges) > 0
  tags                          = local.tags

  dynamic "network_acls" {
    for_each = local.cognitive_network_acls
    content {
      default_action = "Deny"
      ip_rules       = var.allowed_ip_ranges
    }
  }
}

resource "azurerm_cognitive_account" "language" {
  count                         = var.enable_language_pii ? 1 : 0
  name                          = "lang-${local.base}-${random_string.suffix.result}"
  location                      = local.location
  resource_group_name           = local.resource_group_name
  kind                          = "TextAnalytics"
  sku_name                      = "S"
  custom_subdomain_name         = "lang-${local.base}-${random_string.suffix.result}"
  local_auth_enabled            = false
  public_network_access_enabled = !local.private || length(var.allowed_ip_ranges) > 0
  tags                          = local.tags

  dynamic "network_acls" {
    for_each = local.cognitive_network_acls
    content {
      default_action = "Deny"
      ip_rules       = var.allowed_ip_ranges
    }
  }
}

resource "azurerm_role_assignment" "func_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_docintel" {
  scope                = azurerm_cognitive_account.docintel.id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_language" {
  count                = var.enable_language_pii ? 1 : 0
  scope                = azurerm_cognitive_account.language[0].id
  role_definition_name = "Cognitive Services User"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

# Le déployeur peut lancer le CLI local (cli.py) contre les mêmes services.
resource "azurerm_role_assignment" "deployer_openai" {
  count                = var.grant_deployer_data_access ? 1 : 0
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_docintel" {
  count                = var.grant_deployer_data_access ? 1 : 0
  scope                = azurerm_cognitive_account.docintel.id
  role_definition_name = "Cognitive Services User"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "deployer_language" {
  count                = var.grant_deployer_data_access && var.enable_language_pii ? 1 : 0
  scope                = azurerm_cognitive_account.language[0].id
  role_definition_name = "Cognitive Services User"
  principal_id         = data.azurerm_client_config.current.object_id
}
