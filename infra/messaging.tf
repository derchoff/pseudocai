# Storage d'origine, container d'entrée (BlobCreated) -> Event Grid -> Service Bus -> routeur -> files par étape

resource "azurerm_servicebus_namespace" "main" {
  name                          = "sb-${local.base}-${random_string.suffix.result}"
  location                      = local.location
  resource_group_name           = local.resource_group_name
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
#   mail-jobs      : mails .eml à découper (créée même si les mails sont désactivés : vide)
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

resource "azurerm_servicebus_queue" "mail_jobs" {
  name                                 = "mail-jobs"
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
  accepted_extensions = concat(
    [".pdf"],
    var.enable_image_processing ? var.image_extensions : [],
    var.enable_mail_processing ? [".eml"] : [],
  )
}

# Topic système du compte d'origine (un seul autorisé par compte Azure) : créé dans le resource
# group du compte, ou réutilisé s'il existe déjà (source_system_topic_name).
resource "azurerm_eventgrid_system_topic" "source" {
  count    = var.source_system_topic_name == null ? 1 : 0
  provider = azurerm.source
  # Nom historique conservé pour le compte de données : pas de recréation lors de la mise à jour.
  name                = local.source_external ? "egst-${local.base}-source" : "egst-${local.base}-data"
  location            = local.source_location
  resource_group_name = local.source_resource_group_name
  source_resource_id  = local.source_account_id
  topic_type          = "Microsoft.Storage.StorageAccounts"
  tags                = local.tags

  identity {
    type = "SystemAssigned"
  }
}

data "azurerm_eventgrid_system_topic" "source" {
  count               = var.source_system_topic_name == null ? 0 : 1
  provider            = azurerm.source
  name                = var.source_system_topic_name
  resource_group_name = local.source_resource_group_name
}

moved {
  from = azurerm_eventgrid_system_topic.data
  to   = azurerm_eventgrid_system_topic.source[0]
}

locals {
  source_topic_name = (
    var.source_system_topic_name == null ? azurerm_eventgrid_system_topic.source[0].name : data.azurerm_eventgrid_system_topic.source[0].name
  )
  source_topic_principal_id = (
    var.source_system_topic_name == null
    ? azurerm_eventgrid_system_topic.source[0].identity[0].principal_id
    : one(data.azurerm_eventgrid_system_topic.source[0].identity[*].principal_id)
  )
}

resource "azurerm_role_assignment" "eventgrid_sb_sender" {
  scope                = azurerm_servicebus_queue.incoming_events.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = local.source_topic_principal_id
}

resource "azurerm_eventgrid_system_topic_event_subscription" "incoming_created" {
  provider = azurerm.source
  # Nom propre au déploiement sur un topic partagé (plusieurs environnements sur le même compte).
  name                 = var.source_system_topic_name == null ? "incoming-created-to-servicebus" : "${local.base}-to-servicebus"
  system_topic         = local.source_topic_name
  resource_group_name  = local.source_resource_group_name
  service_bus_queue_id = azurerm_servicebus_queue.incoming_events.id

  included_event_types = ["Microsoft.Storage.BlobCreated"]

  subject_filter {
    subject_begins_with = "/blobServices/default/containers/${local.source_container}/blobs/"
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

# App principale : reçoit incoming-events, pdf-jobs et mail-jobs ; envoie vers pdf-jobs, image-jobs
# et mail-jobs.
resource "azurerm_role_assignment" "func_sb_receiver" {
  for_each = {
    incoming = azurerm_servicebus_queue.incoming_events.id
    pdf      = azurerm_servicebus_queue.pdf_jobs.id
    mail     = azurerm_servicebus_queue.mail_jobs.id
  }
  scope                = each.value
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_sb_sender" {
  for_each = {
    pdf   = azurerm_servicebus_queue.pdf_jobs.id
    image = azurerm_servicebus_queue.image_jobs.id
    mail  = azurerm_servicebus_queue.mail_jobs.id
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
