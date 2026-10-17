# Deux Function Apps Flex Consumption :
# - principale : étape 1 (routage du container d'entrée) et étape 3 (pseudonymisation) ;
# - images (si enable_image_processing) : étape 2 (préparation des images, dépendances OpenCV).

locals {
  # Paramètres communs aux deux apps : stockage, files, traces, rangement.
  common_app_settings = {
    ServiceBusConnection__fullyQualifiedNamespace = "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
    ServiceBusConnection__credential              = "managedidentity"
    INCOMING_QUEUE_NAME                           = azurerm_servicebus_queue.incoming_events.name
    PDF_QUEUE_NAME                                = azurerm_servicebus_queue.pdf_jobs.name
    IMAGE_QUEUE_NAME                              = azurerm_servicebus_queue.image_jobs.name
    MAIL_QUEUE_NAME                               = azurerm_servicebus_queue.mail_jobs.name
    TEXT_QUEUE_NAME                               = azurerm_servicebus_queue.text_jobs.name

    STORAGE_ACCOUNT_URL         = azurerm_storage_account.data.primary_blob_endpoint
    SOURCE_STORAGE_ACCOUNT_URL  = local.source_blob_endpoint
    INCOMING_CONTAINER          = local.source_container
    WORK_CONTAINER              = local.containers.work
    OUTPUT_CONTAINER            = local.containers.pseudonymised
    QUARANTINE_CONTAINER        = local.containers.quarantine
    CONFIG_CONTAINER            = local.containers.config
    DELETE_SOURCE_AFTER_SUCCESS = tostring(var.delete_source_after_success)
    IMAGE_PROCESSING_ENABLED    = tostring(var.enable_image_processing)
    MAIL_PROCESSING_ENABLED     = tostring(var.enable_mail_processing)
    JSON_METADATA_ENABLED       = tostring(var.enable_json_metadata)
    MAIL_IMAGE_MIN_SIDE         = tostring(var.mail_image_min_side)
    IMAGE_EXTENSIONS            = join(",", var.image_extensions)

    COSMOS_ENDPOINT          = azurerm_cosmosdb_account.main.endpoint
    COSMOS_DATABASE          = azurerm_cosmosdb_sql_database.main.name
    COSMOS_MAPPING_CONTAINER = azurerm_cosmosdb_sql_container.mappings.name
    COSMOS_TRACE_CONTAINER   = azurerm_cosmosdb_sql_container.traces.name
    # Écrit par l'étape de pseudonymisation (app principale) uniquement.
    COSMOS_OCCURRENCE_CONTAINER = azurerm_cosmosdb_sql_container.occurrences.name
  }
}

# ------------------------------------------------------------------ app principale

module "function_main" {
  source = "./modules/flex_function"

  name                           = "func-${local.base}-${random_string.suffix.result}"
  plan_name                      = "asp-${local.base}"
  resource_group_name            = local.resource_group_name
  location                       = local.location
  subscription_id                = var.subscription_id
  storage_account_name           = azurerm_storage_account.func.name
  storage_blob_endpoint          = azurerm_storage_account.func.primary_blob_endpoint
  deployment_container_name      = azurerm_storage_container.deployments.name
  identity_id                    = azurerm_user_assigned_identity.func.id
  identity_client_id             = azurerm_user_assigned_identity.func.client_id
  app_insights_connection_string = azurerm_application_insights.main.connection_string
  subnet_id                      = local.private ? azurerm_subnet.func[0].id : null
  max_instances                  = var.function_max_instances
  memory_mb                      = var.function_instance_memory_mb
  tags                           = local.tags

  app_settings = merge(
    local.common_app_settings,
    {
      # function_app.py n'enregistre que route_incoming et pseudonymise_job.
      FUNCTION_APP_ROLE              = "main"
      PSEUDO_OUTPUT_MODE             = var.pseudo_output_mode
      HANDWRITING_POLICY             = var.handwriting_policy
      ServiceBusConnection__clientId = azurerm_user_assigned_identity.func.client_id
      PROMPTS_PREFIX                 = "prompts/"

      DOCINTEL_ENDPOINT       = azurerm_cognitive_account.docintel.endpoint
      OPENAI_ENDPOINT         = azurerm_cognitive_account.openai.endpoint
      OPENAI_DEPLOYMENT       = azurerm_cognitive_deployment.llm.name
      OPENAI_API_VERSION      = var.openai_api_version
      OPENAI_REASONING_EFFORT = var.openai_reasoning_effort
      LANGUAGE_ENDPOINT       = var.enable_language_pii ? azurerm_cognitive_account.language[0].endpoint : ""

      KEY_VAULT_URL          = azurerm_key_vault.main.vault_uri
      HMAC_SECRET_NAME       = azurerm_key_vault_secret.hmac_key.name
      ENCRYPTION_SECRET_NAME = azurerm_key_vault_secret.encryption_key.name
    },
    var.pipeline_settings,
  )

  depends_on = [
    time_sleep.rbac_propagation,
    azurerm_role_assignment.func_storage_queue,
    azurerm_role_assignment.func_storage_table,
  ]
}

# Reprise de l'app existante dans le module, sans la recréer.
moved {
  from = azurerm_service_plan.func
  to   = module.function_main.azurerm_service_plan.this
}

moved {
  from = azurerm_function_app_flex_consumption.main
  to   = module.function_main.azurerm_function_app_flex_consumption.this
}

moved {
  from = terraform_data.remove_webjobs_storage_connection_string
  to   = module.function_main.terraform_data.remove_webjobs_storage_connection_string
}

# ------------------------------------------------------------------ app images (optionnelle)

module "function_images" {
  source = "./modules/flex_function"
  count  = var.enable_image_processing ? 1 : 0

  name                           = "func-${local.base}-${random_string.suffix.result}-img"
  plan_name                      = "asp-${local.base}-img"
  resource_group_name            = local.resource_group_name
  location                       = local.location
  subscription_id                = var.subscription_id
  storage_account_name           = azurerm_storage_account.func.name
  storage_blob_endpoint          = azurerm_storage_account.func.primary_blob_endpoint
  deployment_container_name      = azurerm_storage_container.deployments_images[0].name
  identity_id                    = azurerm_user_assigned_identity.func_images[0].id
  identity_client_id             = azurerm_user_assigned_identity.func_images[0].client_id
  app_insights_connection_string = azurerm_application_insights.main.connection_string
  subnet_id                      = local.private ? azurerm_subnet.func_images[0].id : null
  max_instances                  = var.image_function_max_instances
  memory_mb                      = var.image_function_memory_mb
  tags                           = local.tags

  app_settings = merge(
    local.common_app_settings,
    {
      # Même code source que l'app principale ; function_app.py n'enregistre que prepare_image.
      FUNCTION_APP_ROLE              = "images"
      ServiceBusConnection__clientId = azurerm_user_assigned_identity.func_images[0].client_id
    },
    var.pipeline_settings,
  )

  depends_on = [
    time_sleep.rbac_propagation,
    azurerm_role_assignment.images_storage_runtime,
  ]
}
