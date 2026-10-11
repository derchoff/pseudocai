output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "function_app_name" {
  value = azurerm_function_app_flex_consumption.main.name
}

output "data_storage_account_name" {
  value = azurerm_storage_account.data.name
}

output "containers" {
  value = local.containers
}

output "servicebus_namespace" {
  value = azurerm_servicebus_namespace.main.name
}

output "servicebus_queue" {
  value = azurerm_servicebus_queue.pdf_events.name
}

# Valeurs pour src/local.settings.json (exécution locale de cli.py).
output "local_settings_values" {
  value = {
    STORAGE_ACCOUNT_URL     = azurerm_storage_account.data.primary_blob_endpoint
    DOCINTEL_ENDPOINT       = azurerm_cognitive_account.docintel.endpoint
    OPENAI_ENDPOINT         = azurerm_cognitive_account.openai.endpoint
    OPENAI_DEPLOYMENT       = azurerm_cognitive_deployment.llm.name
    OPENAI_API_VERSION      = var.openai_api_version
    OPENAI_REASONING_EFFORT = var.openai_reasoning_effort
    LANGUAGE_ENDPOINT       = var.enable_language_pii ? azurerm_cognitive_account.language[0].endpoint : ""
    KEY_VAULT_URL           = azurerm_key_vault.main.vault_uri
    COSMOS_ENDPOINT         = azurerm_cosmosdb_account.main.endpoint
  }
}
