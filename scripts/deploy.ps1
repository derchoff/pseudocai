<#
.SYNOPSIS
  Déploiement complet : infrastructure (Terraform), prompts (Blob) et code (Azure Functions).
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
$storage = $outputs.data_storage_account_name.value

& "$PSScriptRoot/publish-prompts.ps1" -StorageAccount $storage

if (-not $SkipCode) {
    Push-Location src
    try {
        # Build distant (pip install côté Azure) : les roues Linux de PyMuPDF sont récupérées sur place.
        func azure functionapp publish $functionApp --python
        if ($LASTEXITCODE) { throw "Publication de la Function échouée" }
    } finally {
        Pop-Location
    }
}
Write-Host "Déploiement terminé. Déposer un PDF dans le container 'incoming' de $storage pour tester."
