# Function App Flex Consumption (Python 3.12) avec identité managée affectée par l'utilisateur,
# stockage runtime et déploiement par identité, et contournement du paramètre AzureWebJobsStorage.
# Utilisé pour l'app principale (routage + pseudonymisation) et l'app images (préparation).

terraform {
  required_providers {
    azurerm = {
      source = "hashicorp/azurerm"
    }
  }
}

resource "azurerm_service_plan" "this" {
  name                = var.plan_name
  location            = var.location
  resource_group_name = var.resource_group_name
  os_type             = "Linux"
  sku_name            = "FC1" # Flex Consumption : un plan par app
  tags                = var.tags
}

resource "azurerm_function_app_flex_consumption" "this" {
  name                = var.name
  location            = var.location
  resource_group_name = var.resource_group_name
  service_plan_id     = azurerm_service_plan.this.id

  runtime_name    = "python"
  runtime_version = "3.12"

  storage_container_type            = "blobContainer"
  storage_container_endpoint        = "${var.storage_blob_endpoint}${var.deployment_container_name}"
  storage_authentication_type       = "UserAssignedIdentity"
  storage_user_assigned_identity_id = var.identity_id

  maximum_instance_count = var.max_instances
  instance_memory_in_mb  = var.memory_mb

  virtual_network_subnet_id = var.subnet_id
  # Aucun déclencheur HTTP n'est exposé ; l'accès public sert uniquement au déploiement (SCM,
  # authentification Entra ID obligatoire puisque l'auth basique est désactivée).
  public_network_access_enabled = true
  https_only                    = true

  webdeploy_publish_basic_authentication_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [var.identity_id]
  }

  site_config {
    application_insights_connection_string = var.app_insights_connection_string
    minimum_tls_version                    = "1.2"
  }

  app_settings = merge(
    {
      # Identité managée utilisée par DefaultAzureCredential, le runtime et les déclencheurs.
      AZURE_CLIENT_ID                  = var.identity_client_id
      AzureWebJobsStorage__accountName = var.storage_account_name
      AzureWebJobsStorage__credential  = "managedidentity"
      AzureWebJobsStorage__clientId    = var.identity_client_id
    },
    var.app_settings,
  )

  tags = var.tags
}

# Contournement d'un comportement du provider azurerm (5.x) : pour une app Flex Consumption, il
# écrit toujours un paramètre AzureWebJobsStorage = "DefaultEndpointsProtocol=...;AccountKey=;..."
# (clé vide), même avec storage_authentication_type = "UserAssignedIdentity". Cette chaîne est
# prioritaire sur les paramètres AzureWebJobsStorage__* (identité managée) : l'hôte Functions
# tente alors une authentification par clé, refusée, et ne démarre pas (« unhealthy »).
# Le paramètre est supprimé après chaque création ou modification de l'app (le provider le réécrit
# à chaque mise à jour). Nécessite Azure CLI connecté sur le poste qui exécute Terraform ;
# --subscription garantit que la commande vise bien l'abonnement du déploiement.
resource "terraform_data" "remove_webjobs_storage_connection_string" {
  triggers_replace = [sha1(jsonencode(azurerm_function_app_flex_consumption.this))]

  provisioner "local-exec" {
    command = join(" ", [
      "az functionapp config appsettings delete",
      "--subscription ${var.subscription_id}",
      "--resource-group ${var.resource_group_name}",
      "--name ${azurerm_function_app_flex_consumption.this.name}",
      "--setting-names AzureWebJobsStorage",
      "--output none",
    ])
  }
}
