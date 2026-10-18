output "resource_group_name" {
  value = local.resource_group_name
}

output "function_app_name" {
  value = module.function_main.name
}

output "image_function_app_name" {
  description = "App images (null si enable_image_processing = false)."
  value       = var.enable_image_processing ? module.function_images[0].name : null
}

output "data_storage_account_name" {
  value = azurerm_storage_account.data.name
}

output "source_storage_account_name" {
  description = "Compte où déposer les documents (le compte « data » si aucun compte d'origine n'est fourni)."
  value       = local.source_account_name
}

output "source_container_name" {
  description = "Container où déposer les documents."
  value       = local.source_container
}

output "containers" {
  value = local.containers
}

output "cosmos_account_name" {
  description = "Compte Cosmos DB (scripts/statistiques.py -Cosmos)."
  value       = azurerm_cosmosdb_account.main.name
}

output "servicebus_namespace" {
  value = azurerm_servicebus_namespace.main.name
}

output "servicebus_queues" {
  value = {
    incoming = azurerm_servicebus_queue.incoming_events.name
    pdf      = azurerm_servicebus_queue.pdf_jobs.name
    image    = azurerm_servicebus_queue.image_jobs.name
    mail     = azurerm_servicebus_queue.mail_jobs.name
    text     = azurerm_servicebus_queue.text_jobs.name
  }
}

# Valeurs pour src/local.settings.json (exécution locale de cli.py).
output "local_settings_values" {
  value = {
    STORAGE_ACCOUNT_URL        = azurerm_storage_account.data.primary_blob_endpoint
    SOURCE_STORAGE_ACCOUNT_URL = local.source_blob_endpoint
    INCOMING_CONTAINER         = local.source_container
    DOCINTEL_ENDPOINT          = azurerm_cognitive_account.docintel.endpoint
    OPENAI_ENDPOINT            = azurerm_cognitive_account.openai.endpoint
    OPENAI_DEPLOYMENT          = azurerm_cognitive_deployment.llm.name
    OPENAI_API_VERSION         = var.openai_api_version
    OPENAI_REASONING_EFFORT    = var.openai_reasoning_effort
    LANGUAGE_ENDPOINT          = var.enable_language_pii ? azurerm_cognitive_account.language[0].endpoint : ""
    KEY_VAULT_URL              = azurerm_key_vault.main.vault_uri
    COSMOS_ENDPOINT            = azurerm_cosmosdb_account.main.endpoint
  }
}
