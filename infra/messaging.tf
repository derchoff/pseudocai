# Storage (BlobCreated) -> Event Grid -> Service Bus -> Function

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

resource "azurerm_servicebus_queue" "pdf_events" {
  name                                 = "pdf-events"
  namespace_id                         = azurerm_servicebus_namespace.main.id
  lock_duration                        = "PT5M"
  max_delivery_count                   = var.servicebus_max_delivery_count
  default_message_ttl                  = "P7D"
  dead_lettering_on_message_expiration = true
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
  scope                = azurerm_servicebus_queue.pdf_events.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_eventgrid_system_topic.data.identity[0].principal_id
}

resource "azurerm_eventgrid_system_topic_event_subscription" "pdf_created" {
  name                 = "pdf-created-to-servicebus"
  system_topic         = azurerm_eventgrid_system_topic.data.name
  resource_group_name  = azurerm_resource_group.main.name
  service_bus_queue_id = azurerm_servicebus_queue.pdf_events.id

  included_event_types = ["Microsoft.Storage.BlobCreated"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/${local.containers.incoming}/blobs/"
    subject_ends_with   = ".pdf"
    case_sensitive      = false
  }

  # Uniquement les blobs complètement écrits.
  advanced_filter {
    string_in {
      key    = "data.api"
      values = ["PutBlob", "PutBlockList", "CopyBlob", "FlushWithClose"]
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

resource "azurerm_role_assignment" "func_sb_receiver" {
  scope                = azurerm_servicebus_queue.pdf_events.id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}
