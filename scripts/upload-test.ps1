<#
.SYNOPSIS
  Dépose un document (PDF ou image) dans le container d'entrée du storage d'origine pour déclencher le traitement.
.EXAMPLE
  ./scripts/upload-test.ps1 -Path ./exemples/facture-dentiste.pdf
  ./scripts/upload-test.ps1 -Path ./exemples/photo-facture-dentiste.jpg
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$StorageAccount = $(terraform "-chdir=infra" output "-raw" source_storage_account_name),
    [string]$Container = $(terraform "-chdir=infra" output "-raw" source_container_name)
)
$ErrorActionPreference = "Stop"
$name = Split-Path -Leaf $Path
az storage blob upload --account-name $StorageAccount --auth-mode login `
    --container-name $Container --name $name --file $Path --overwrite --only-show-errors | Out-Null
if ($LASTEXITCODE) { throw "Échec du dépôt" }
Write-Host "Déposé : $StorageAccount/$Container/$name. Résultat attendu dans pseudonymised/<type>/ (ou quarantine/<type>/) d'ici 1 à 2 minutes."
