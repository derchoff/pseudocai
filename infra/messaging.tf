# Storage incoming/ (BlobCreated) -> Event Grid -> Service Bus -> routeur -> files par étape

resource "azurerm_servicebus_namespace" "main" {
  name                          = "sb-${local.base}-${random_string.suffix.result}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = local.private ? "Premium" : "Standard" # private endpoint = Premium
  capacity                      = local.private ? 1 : 0
  premium_messaging_partitions  = local.private ? 1 : 0
  local_auth_enabled            = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = !local.private || length(var.allowed_ip_ranges) > 0
  tags                          = local.tags

  dynamic "network_rule_set" {
    for_each = local.private ? [1] : []
    content {
      default_action                = "Deny"
      public_network_access_enabled = length(var.allowed_ip_ranges) > 0
      trusted_services_allowed      = true # livraison Event Grid via identité managée
      ip_rules                      = var.allowed_ip_ranges
    }
  }
}

# Une file par étape. Chaque file a sa dead-letter (messages en échec après max_delivery_count).
#   incoming-events : événements BlobCreated de incoming/  -> routeur
#   pdf-jobs       : jobs de pseudonymisation (PDF, ou image préparée)
#   image-jobs     : jobs de préparation d'image (créée même si les images sont désactivées : vide)
resource "azurerm_servicebus_queue" "incoming_events" {
  name                                 = "incoming-events"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  lock_duration                        = "PT1M"
  max_delivery_count                   = var.servicebus_max_delivery_count
  default_message_ttl                  = "P7D"
  dead_lettering_on_message_expiration = true
}

resource "azurerm_servicebus_queue" "pdf_jobs" {
  name                                 = "pdf-jobs"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  lock_duration                        = "PT5M"
  max_delivery_count                   = var.servicebus_max_delivery_count
  default_message_ttl                  = "P7D"
  dead_lettering_on_message_expiration = true
}

resource "azurerm_servicebus_queue" "image_jobs" {
  name                                 = "image-jobs"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  lock_duration                        = "PT5M"
  max_delivery_count                   = var.servicebus_max_delivery_count
  default_message_ttl                  = "P7D"
  dead_lettering_on_message_expiration = true
}

locals {
  # Extensions transmises par Event Grid (comparaison insensible à la casse : .JPG est accepté).
  # Le routeur vérifie ensuite le format réel du fichier (signature), pas seulement l'extension.
  accepted_extensions = concat([".pdf"], var.enable_image_processing ? var.image_extensions : [])
}

resource "azurerm_eventgrid_system_topic" "data" {
  name                = "egst-${local.base}-data"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  source_resource_id  = azurerm_storage_account.data.id
  topic_type          = "Microsoft.Storage.StorageAccounts"
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "eventgrid_sb_sender" {
  scope                = azurerm_servicebus_queue.incoming_events.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_eventgrid_system_topic.data.identity[0].principal_id
}

resource "azurerm_eventgrid_system_topic_event_subscription" "incoming_created" {
  name                 = "incoming-created-to-servicebus"
  system_topic         = azurerm_eventgrid_system_topic.data.name
  resource_group_name  = azurerm_resource_group.main.name
  service_bus_queue_id = azurerm_servicebus_queue.incoming_events.id

  included_event_types = ["Microsoft.Storage.BlobCreated"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/${local.containers.incoming}/blobs/"
    case_sensitive      = false
  }

  advanced_filter {
    # Uniquement les blobs complètement écrits.
    string_in {
      key    = "data.api"
      values = ["PutBlob", "PutBlockList", "CopyBlob", "FlushWithClose"]
    }
    # Uniquement les extensions prises en charge (PDF, et images si activées).
    string_ends_with {
      key    = "subject"
      values = local.accepted_extensions
    }
  }

  delivery_identity {
    type = "SystemAssigned"
  }

  retry_policy {
    max_delivery_attempts = 30
    event_time_to_live    = 1440
  }

  depends_on = [time_sleep.rbac_propagation]
}

# ------------------------------------------------------------------ droits Service Bus

# App principale : reçoit incoming-events et pdf-jobs ; envoie vers pdf-jobs et image-jobs.
resource "azurerm_role_assignment" "func_sb_receiver" {
  for_each = {
    incoming = azurerm_servicebus_queue.incoming_events.id
    pdf      = azurerm_servicebus_queue.pdf_jobs.id
  }
  scope                = each.value
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_sb_sender" {
  for_each = {
    pdf   = azurerm_servicebus_queue.pdf_jobs.id
    image = azurerm_servicebus_queue.image_jobs.id
  }
  scope                = each.value
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

# App images : reçoit image-jobs ; envoie vers pdf-jobs.
resource "azurerm_role_assignment" "images_sb_receiver" {
  count                = var.enable_image_processing ? 1 : 0
  scope                = azurerm_servicebus_queue.image_jobs.id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.func_images[0].principal_id
}

resource "azurerm_role_assignment" "images_sb_sender" {
  count                = var.enable_image_processing ? 1 : 0
  scope                = azurerm_servicebus_queue.pdf_jobs.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_user_assigned_identity.func_images[0].principal_id
}
