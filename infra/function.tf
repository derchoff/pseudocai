resource "azurerm_service_plan" "func" {
  name                = "asp-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  os_type             = "Linux"
  sku_name            = "FC1"
  tags                = local.tags
}

resource "azurerm_function_app_flex_consumption" "main" {
  name                = "func-${local.base}-${random_string.suffix.result}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.func.id

  runtime_name    = "python"
  runtime_version = "3.12"

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${azurerm_storage_account.func.primary_blob_endpoint}${azurerm_storage_container.deployments.name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = azurerm_user_assigned_identity.func.id

  maximum_instance_count = var.function_max_instances
  instance_memory_in_mb  = var.function_instance_memory_mb

  virtual_network_subnet_id = local.private ? azurerm_subnet.func[0].id : null
  # Aucun déclencheur HTTP n'est exposé ; l'accès public sert uniquement au déploiement (SCM,
  # authentification Entra ID obligatoire puisque l'auth basique est désactivée).
  public_network_access_enabled = true
  https_only                    = true

  webdeploy_publish_basic_authentication_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    minimum_tls_version                    = "1.2"
  }

  app_settings = merge(
    {
      # Identité managée utilisée par DefaultAzureCredential et par les déclencheurs.
      AZURE_CLIENT_ID = azurerm_user_assigned_identity.func.client_id

      AzureWebJobsStorage__accountName = azurerm_storage_account.func.name
      AzureWebJobsStorage__credential  = "managedidentity"
      AzureWebJobsStorage__clientId    = azurerm_user_assigned_identity.func.client_id

      ServiceBusConnection__fullyQualifiedNamespace = "${azurerm_servicebus_namespace.main.name}.servicebus.windows.net"
      ServiceBusConnection__credential              = "managedidentity"
      ServiceBusConnection__clientId                = azurerm_user_assigned_identity.func.client_id
      SERVICEBUS_QUEUE_NAME                         = azurerm_servicebus_queue.pdf_events.name

      STORAGE_ACCOUNT_URL         = azurerm_storage_account.data.primary_blob_endpoint
      INPUT_CONTAINER             = local.containers.incoming
      OUTPUT_CONTAINER            = local.containers.pseudonymised
      QUARANTINE_CONTAINER        = local.containers.quarantine
      CONFIG_CONTAINER            = local.containers.config
      PROMPTS_PREFIX              = "prompts/"
      DELETE_SOURCE_AFTER_SUCCESS = tostring(var.delete_source_after_success)

      DOCINTEL_ENDPOINT       = azurerm_cognitive_account.docintel.endpoint
      OPENAI_ENDPOINT         = azurerm_cognitive_account.openai.endpoint
      OPENAI_DEPLOYMENT       = azurerm_cognitive_deployment.llm.name
      OPENAI_API_VERSION      = var.openai_api_version
      OPENAI_REASONING_EFFORT = var.openai_reasoning_effort
      LANGUAGE_ENDPOINT       = var.enable_language_pii ? azurerm_cognitive_account.language[0].endpoint : ""

      KEY_VAULT_URL          = azurerm_key_vault.main.vault_uri
      HMAC_SECRET_NAME       = azurerm_key_vault_secret.hmac_key.name
      ENCRYPTION_SECRET_NAME = azurerm_key_vault_secret.encryption_key.name

      COSMOS_ENDPOINT          = azurerm_cosmosdb_account.main.endpoint
      COSMOS_DATABASE          = azurerm_cosmosdb_sql_database.main.name
      COSMOS_MAPPING_CONTAINER = azurerm_cosmosdb_sql_container.mappings.name
      COSMOS_TRACE_CONTAINER   = azurerm_cosmosdb_sql_container.traces.name
    },
    var.pipeline_settings,
  )

  tags = local.tags

  depends_on = [
    time_sleep.rbac_propagation,
    azurerm_role_assignment.func_storage_queue,
    azurerm_role_assignment.func_storage_table,
  ]
}

# Contournement d'un comportement du provider azurerm (5.x) : pour une app Flex Consumption, il
# écrit toujours un paramètre AzureWebJobsStorage = "DefaultEndpointsProtocol=...;AccountKey=;..."
# (clé vide), même avec storage_authentication_type = "UserAssignedIdentity". Cette chaîne est
# prioritaire sur les paramètres AzureWebJobsStorage__* (identité managée) : l'hôte Functions
# tente alors une authentification par clé, refusée, et ne démarre pas (« unhealthy »).
# Le paramètre est supprimé après chaque création ou modification de la Function (le provider
# le réécrit à chaque mise à jour). Nécessite Azure CLI connecté sur le poste qui exécute Terraform ;
# --subscription garantit que la commande vise bien l'abonnement du déploiement.
resource "terraform_data" "remove_webjobs_storage_connection_string" {
  triggers_replace = [sha1(jsonencode(azurerm_function_app_flex_consumption.main))]

  provisioner "local-exec" {
    command = join(" ", [
      "az functionapp config appsettings delete",
      "--subscription ${var.subscription_id}",
      "--resource-group ${azurerm_resource_group.main.name}",
      "--name ${azurerm_function_app_flex_consumption.main.name}",
      "--setting-names AzureWebJobsStorage",
      "--output none",
    ])
  }
}
