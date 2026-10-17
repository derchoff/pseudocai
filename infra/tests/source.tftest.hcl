# Tests du choix du resource group et du storage d'origine, sans Azure : providers simulés,
# commande plan uniquement.   terraform -chdir=infra init -backend=false && terraform -chdir=infra test

mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "44444444-4444-4444-4444-444444444444"
      object_id       = "55555555-5555-5555-5555-555555555555"
      subscription_id = "11111111-1111-1111-1111-111111111111"
      client_id       = "66666666-6666-6666-6666-666666666666"
    }
  }
}
mock_provider "random" {}
mock_provider "time" {}

mock_provider "azurerm" {
  alias = "source"

  mock_data "azurerm_storage_account" {
    defaults = {
      id                    = "/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/rg-metier/providers/Microsoft.Storage/storageAccounts/stmetier"
      location              = "westeurope"
      primary_blob_endpoint = "https://stmetier.blob.core.windows.net/"
    }
  }
}

variables {
  subscription_id = "11111111-1111-1111-1111-111111111111"
}

run "defaut_storage_interne" {
  command = plan

  assert {
    condition     = local.resource_group_name_wanted == "rg-pseudo-dev" && length(azurerm_resource_group.main) == 1
    error_message = "Sans variable, le resource group rg-<project>-<environment> est créé."
  }
  assert {
    condition     = contains(keys(local.containers), "incoming") && local.source_container == "incoming"
    error_message = "Sans compte d'origine, le container incoming est créé dans le compte data."
  }
  assert {
    condition     = local.source_subscription_id == var.subscription_id && length(data.azurerm_storage_account.source) == 0
    error_message = "Sans compte d'origine, aucun compte existant n'est lu."
  }
  assert {
    condition     = length(azurerm_eventgrid_system_topic.source) == 1 && azurerm_eventgrid_system_topic.source[0].name == "egst-pseudo-dev-data"
    error_message = "Le topic Event Grid est créé, avec son nom historique (pas de recréation à la mise à jour)."
  }
  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.incoming_created.name == "incoming-created-to-servicebus"
    error_message = "Le nom de l'abonnement Event Grid existant est conservé."
  }
  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.incoming_created.subject_filter[0].subject_begins_with == "/blobServices/default/containers/incoming/blobs/"
    error_message = "Le filtre Event Grid vise le container d'entrée."
  }
  assert {
    condition     = azurerm_role_assignment.func_source.role_definition_name == "Storage Blob Data Reader"
    error_message = "Lecture seule sur l'entrée quand la source n'est pas supprimée."
  }
}

run "resource_group_existant" {
  command = plan

  variables {
    resource_group_name   = "rg-client-existant"
    create_resource_group = false
  }

  assert {
    condition     = length(azurerm_resource_group.main) == 0 && length(data.azurerm_resource_group.existing) == 1
    error_message = "Un resource group existant est lu, pas créé."
  }
  assert {
    condition     = azurerm_storage_account.data.resource_group_name == "rg-client-existant"
    error_message = "Les ressources sont créées dans le resource group existant."
  }
}

run "compte_origine_existant" {
  command = plan

  variables {
    source_storage_account_id   = "/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/rg-metier/providers/Microsoft.Storage/storageAccounts/stmetier"
    source_container_name       = "factures-entrantes"
    delete_source_after_success = true
  }

  assert {
    condition     = local.source_subscription_id == "22222222-2222-2222-2222-222222222222"
    error_message = "Le provider du compte d'origine vise son abonnement."
  }
  assert {
    condition     = !contains(keys(local.containers), "incoming") && length(azurerm_storage_container.source) == 0
    error_message = "Aucun container d'entrée créé : ni dans data, ni dans le compte existant."
  }
  assert {
    condition = (
      azurerm_eventgrid_system_topic.source[0].name == "egst-pseudo-dev-source"
      && azurerm_eventgrid_system_topic.source[0].resource_group_name == "rg-metier"
      && azurerm_eventgrid_system_topic.source[0].location == "westeurope"
    )
    error_message = "Le topic Event Grid est créé dans le resource group et la région du compte d'origine."
  }
  assert {
    condition     = azurerm_eventgrid_system_topic_event_subscription.incoming_created.subject_filter[0].subject_begins_with == "/blobServices/default/containers/factures-entrantes/blobs/"
    error_message = "Le filtre Event Grid vise le container choisi."
  }
  assert {
    condition = (
      azurerm_role_assignment.func_source.scope == "/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/rg-metier/providers/Microsoft.Storage/storageAccounts/stmetier/blobServices/default/containers/factures-entrantes"
      && azurerm_role_assignment.func_source.role_definition_name == "Storage Blob Data Contributor"
    )
    error_message = "Droit d'écriture sur le container choisi, car la source est supprimée après traitement."
  }
  assert {
    condition = (
      local.common_app_settings.SOURCE_STORAGE_ACCOUNT_URL == "https://stmetier.blob.core.windows.net/"
      && local.common_app_settings.INCOMING_CONTAINER == "factures-entrantes"
    )
    error_message = "Les Functions lisent le compte et le container d'origine."
  }
  assert {
    condition     = length([for r in azurerm_storage_management_policy.data.rule : r if r.name == "purge-incoming"]) == 0
    error_message = "Aucune purge n'est posée pour un compte d'origine existant."
  }
}

run "topic_existant_et_container_cree" {
  command = plan

  variables {
    source_storage_account_id = "/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/rg-metier/providers/Microsoft.Storage/storageAccounts/stmetier"
    source_system_topic_name  = "egst-metier"
    create_source_container   = true
  }

  override_data {
    target = data.azurerm_eventgrid_system_topic.source[0]
    values = {
      name     = "egst-metier"
      identity = [{ type = "SystemAssigned", principal_id = "33333333-3333-3333-3333-333333333333", tenant_id = "t", identity_ids = [] }]
    }
  }

  assert {
    condition     = length(azurerm_eventgrid_system_topic.source) == 0 && length(azurerm_storage_container.source) == 1
    error_message = "Le topic existant est réutilisé ; le container est créé à la demande."
  }
  assert {
    condition = (
      azurerm_eventgrid_system_topic_event_subscription.incoming_created.system_topic == "egst-metier"
      && azurerm_eventgrid_system_topic_event_subscription.incoming_created.name == "pseudo-dev-to-servicebus"
    )
    error_message = "L'abonnement est ajouté au topic existant, avec un nom propre au déploiement."
  }
  assert {
    condition     = azurerm_role_assignment.eventgrid_sb_sender.principal_id == "33333333-3333-3333-3333-333333333333"
    error_message = "L'identité du topic existant peut écrire dans la file Service Bus."
  }
}

run "mails_actives" {
  command = plan

  variables {
    enable_mail_processing = true
  }

  assert {
    condition = (
      contains(local.accepted_extensions, ".eml")
      && local.common_app_settings.MAIL_PROCESSING_ENABLED == "true"
      && local.common_app_settings.MAIL_QUEUE_NAME == "mail-jobs"
    )
    error_message = "Mails activés : .eml dans le filtre Event Grid, réglages des Functions."
  }
}

run "json_metadata" {
  command = plan

  assert {
    condition = (
      contains(local.accepted_extensions, ".json")
      && local.common_app_settings.JSON_METADATA_ENABLED == "true"
      && local.common_app_settings.TEXT_QUEUE_NAME == "text-jobs"
    )
    error_message = "JSON de métadonnées activés par défaut : .json dans le filtre Event Grid, file text-jobs."
  }
  assert {
    condition = (
      contains([for r in azurerm_storage_management_policy.data.rule : r.name], "purge-json-metadata")
      && !contains(flatten([for r in azurerm_storage_management_policy.data.rule : r.filters[0].prefix_match if r.name == "purge-work"]), "work/")
    )
    error_message = "work/json-metadata/ a sa propre purge ; purge-work ne vise plus tout work/."
  }
}

run "json_metadata_desactives" {
  command = plan

  variables {
    enable_json_metadata = false
  }

  assert {
    condition     = !contains(local.accepted_extensions, ".json") && local.common_app_settings.JSON_METADATA_ENABLED == "false"
    error_message = "JSON désactivés : .json absent du filtre Event Grid."
  }
}
