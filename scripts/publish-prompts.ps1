<#
.SYNOPSIS
  Publie les prompts (dossier prompts/) dans le container config/prompts/ du compte de données.
  Les Functions rechargent les prompts modifiés en moins de PROMPT_CACHE_SECONDS (5 min par défaut).
#>
param(
    [string]$StorageAccount = $(terraform "-chdir=infra" output "-raw" data_storage_account_name)
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
az storage blob upload-batch `
    --account-name $StorageAccount `
    --auth-mode login `
    --destination config `
    --destination-path prompts `
    --source (Join-Path $root "prompts") `
    --pattern "*.md" `
    --overwrite `
    --only-show-errors | Out-Null
if ($LASTEXITCODE) { throw "Échec de la publication des prompts" }
Write-Host "Prompts publiés dans $StorageAccount/config/prompts/"
