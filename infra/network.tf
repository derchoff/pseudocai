# Réseau privé (optionnel) : VNet, intégration VNet de la Function, private endpoints + DNS.

locals {
  private_dns_zones = local.private ? toset([
    "privatelink.blob.core.windows.net",
    "privatelink.queue.core.windows.net",
    "privatelink.table.core.windows.net",
    "privatelink.vaultcore.azure.net",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.servicebus.windows.net",
    "privatelink.documents.azure.com",
  ]) : toset([])

  private_endpoints = local.private ? merge(
    {
      data-blob = {
        resource_id = azurerm_storage_account.data.id
        subresource = "blob"
        zones       = ["privatelink.blob.core.windows.net"]
      }
      func-blob = {
        resource_id = azurerm_storage_account.func.id
        subresource = "blob"
        zones       = ["privatelink.blob.core.windows.net"]
      }
      func-queue = {
        resource_id = azurerm_storage_account.func.id
        subresource = "queue"
        zones       = ["privatelink.queue.core.windows.net"]
      }
      func-table = {
        resource_id = azurerm_storage_account.func.id
        subresource = "table"
        zones       = ["privatelink.table.core.windows.net"]
      }
      kv = {
        resource_id = azurerm_key_vault.main.id
        subresource = "vault"
        zones       = ["privatelink.vaultcore.azure.net"]
      }
      openai = {
        resource_id = azurerm_cognitive_account.openai.id
        subresource = "account"
        zones       = ["privatelink.openai.azure.com", "privatelink.cognitiveservices.azure.com"]
      }
      docintel = {
        resource_id = azurerm_cognitive_account.docintel.id
        subresource = "account"
        zones       = ["privatelink.cognitiveservices.azure.com"]
      }
      sb = {
        resource_id = azurerm_servicebus_namespace.main.id
        subresource = "namespace"
        zones       = ["privatelink.servicebus.windows.net"]
      }
      cosmos = {
        resource_id = azurerm_cosmosdb_account.main.id
        subresource = "Sql"
        zones       = ["privatelink.documents.azure.com"]
      }
    },
    var.enable_language_pii ? {
      language = {
        resource_id = azurerm_cognitive_account.language[0].id
        subresource = "account"
        zones       = ["privatelink.cognitiveservices.azure.com"]
      }
    } : {}
  ) : {}
}

resource "azurerm_virtual_network" "main" {
  count               = local.private ? 1 : 0
  name                = "vnet-${local.base}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = [var.vnet_address_space]
  tags                = local.tags
}

resource "azurerm_subnet" "func" {
  count                = local.private ? 1 : 0
  name                 = "snet-func"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main[0].name
  address_prefixes     = [cidrsubnet(var.vnet_address_space, 2, 0)]

  delegation {
    name = "flex-consumption"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

# Intégration VNet de l'app images : un sous-réseau délégué par app Flex Consumption.
resource "azurerm_subnet" "func_images" {
  count                = local.private && var.enable_image_processing ? 1 : 0
  name                 = "snet-func-img"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main[0].name
  address_prefixes     = [cidrsubnet(var.vnet_address_space, 2, 2)]

  delegation {
    name = "flex-consumption"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_subnet" "endpoints" {
  count                = local.private ? 1 : 0
  name                 = "snet-pe"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main[0].name
  address_prefixes     = [cidrsubnet(var.vnet_address_space, 2, 1)]
}

resource "azurerm_private_dns_zone" "zones" {
  for_each            = local.private_dns_zones
  name                = each.value
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "links" {
  for_each            = local.private_dns_zones
  name                = "link-${replace(each.value, ".", "-")}"
  private_dns_zone_id = azurerm_private_dns_zone.zones[each.value].id
  virtual_network_id  = azurerm_virtual_network.main[0].id
  tags                = local.tags
}

resource "azurerm_private_endpoint" "pe" {
  for_each            = local.private_endpoints
  name                = "pe-${local.base}-${each.key}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.endpoints[0].id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-${each.key}"
    private_connection_resource_id = each.value.resource_id
    subresource_names              = [each.value.subresource]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "default"
    private_dns_zone_ids = [for z in each.value.zones : azurerm_private_dns_zone.zones[z].id]
  }
}
