terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }

  # Paramètres fournis à l'init : terraform init -backend-config=backend.hcl
  backend "azurerm" {}
}

provider "azurerm" {
  subscription_id = var.subscription_id

  # Les comptes de stockage n'acceptent pas les clés d'accès : Terraform passe par Entra ID.
  storage_use_azuread = true

  features {
    key_vault {
      purge_soft_delete_on_destroy = false
    }
    cognitive_account {
      purge_soft_delete_on_destroy = false
    }
    resource_group {
      prevent_deletion_if_contains_resources = true
    }
  }
}

# Abonnement du compte de stockage d'origine (celui de source_storage_account_id, sinon le même) :
# le topic Event Grid et les droits d'accès y sont créés.
provider "azurerm" {
  alias               = "source"
  subscription_id     = local.source_subscription_id
  storage_use_azuread = true

  features {}
}
