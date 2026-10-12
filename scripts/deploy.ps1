<#
.SYNOPSIS
  Déploiement complet : infrastructure (Terraform), prompts (Blob) et code des Function Apps
  (app principale ; app images si enable_image_processing = true).
.EXAMPLE
  ./scripts/deploy.ps1 -VarFile infra/terraform.tfvars -BackendConfig infra/backend.hcl
  ./scripts/deploy.ps1 -SkipInfra   # code + prompts uniquement
#>
param(
    [string]$VarFile = "infra/terraform.tfvars",
    [string]$BackendConfig = "infra/backend.hcl",
    [switch]$SkipInfra,
    [switch]$SkipCode,
    [switch]$AutoApprove
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $SkipInfra) {
    # Arguments entre guillemets : Windows PowerShell 5.1 coupe sinon "-out=deploy.tfplan" en deux.
    terraform "-chdir=infra" init "-input=false" "-backend-config=$(Resolve-Path $BackendConfig)"
    if ($LASTEXITCODE) { throw "terraform init a échoué" }
    terraform "-chdir=infra" plan "-input=false" "-var-file=$(Resolve-Path $VarFile)" "-out=deploy.tfplan"
    if ($LASTEXITCODE) { throw "terraform plan a échoué" }
    if (-not $AutoApprove) {
        $answer = Read-Host "Appliquer ce plan ? (oui/non)"
        if ($answer -ne "oui") { Write-Host "Abandon."; exit 1 }
    }
    terraform "-chdir=infra" apply "-input=false" "deploy.tfplan"
    if ($LASTEXITCODE) { throw "terraform apply a échoué" }
}

$outputs = terraform "-chdir=infra" output "-json" | ConvertFrom-Json
$functionApp = $outputs.function_app_name.value
$imageApp = $outputs.image_function_app_name.value
$storage = $outputs.data_storage_account_name.value
$sourceStorage = $outputs.source_storage_account_name.value
$sourceContainer = $outputs.source_container_name.value

& "$PSScriptRoot/publish-prompts.ps1" -StorageAccount $storage

function Publish-FunctionApp([string]$Name, [string]$Folder) {
    Push-Location $Folder
    try {
        # Build distant (pip install côté Azure) : les roues Linux (PyMuPDF, OpenCV) sont récupérées sur place.
        func azure functionapp publish $Name --python
        if ($LASTEXITCODE) { throw "Publication de $Name échouée" }
    } finally {
        Pop-Location
    }
}

if (-not $SkipCode) {
    Publish-FunctionApp $functionApp "src"

    if ($imageApp) {
        # Même code source, avec en plus les dépendances OpenCV : copie dans un dossier temporaire
        # dont requirements.txt = requirements.txt + requirements-images.txt.
        $staging = Join-Path ([System.IO.Path]::GetTempPath()) "pseudo-images-$([guid]::NewGuid())"
        try {
            Copy-Item -Path "src" -Destination $staging -Recurse -Exclude ".venv", "__pycache__", "local.settings.json"
            $requirements = (Get-Content "src/requirements.txt") + (Get-Content "src/requirements-images.txt")
            Set-Content -Path (Join-Path $staging "requirements.txt") -Value $requirements -Encoding utf8
            Publish-FunctionApp $imageApp $staging
        } finally {
            Remove-Item -Recurse -Force $staging -ErrorAction SilentlyContinue
        }
    }
}
Write-Host "Déploiement terminé. Déposer un document dans le container '$sourceContainer' de $sourceStorage pour tester."
