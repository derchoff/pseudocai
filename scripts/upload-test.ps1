<#
.SYNOPSIS
  Dépose un PDF dans le container d'entrée pour déclencher le traitement.
.EXAMPLE
  ./scripts/upload-test.ps1 -Path ./exemples/attestation.pdf
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$StorageAccount = $(terraform "-chdir=infra" output "-raw" data_storage_account_name)
)
$ErrorActionPreference = "Stop"
$name = Split-Path -Leaf $Path
az storage blob upload --account-name $StorageAccount --auth-mode login `
    --container-name incoming --name $name --file $Path --overwrite --only-show-errors | Out-Null
if ($LASTEXITCODE) { throw "Échec du dépôt" }
Write-Host "Déposé : incoming/$name. Résultat attendu dans pseudonymised/ (ou quarantine/) d'ici 1 à 2 minutes."
