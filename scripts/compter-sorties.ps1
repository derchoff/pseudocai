<#
.SYNOPSIS
  Compte les documents publiés et en quarantaine, et (option) les messages restant dans les files.
.DESCRIPTION
  Lit la liste des fichiers des containers « pseudonymised » et « quarantine » du compte de stockage
  « data », et les classe :
    1. publié       : un rapport par document publié (PDF ou image : <nom>.rapport.json ; mail :
                      mail-…/rapport.json)
    2. quarantaine  : un motif par document en quarantaine (<nom>.motif.json ; mail : mail-…/motif.json)
    3. publié       : fichiers qui accompagnent un document publié (document pseudonymisé, texte,
                      mail.json, corps du mail, pièces jointes, JSON de métadonnées de la GED)
    4. quarantaine  : originaux conservés en quarantaine
  Total traités = 1 + 2, à comparer au nombre de fichiers déposés (un mail compte pour un fichier,
  quel que soit son nombre de pièces jointes ; un JSON de la GED suit son document).

  Avec -Files, ajoute le nombre de messages en attente et en dead-letter de chaque file Service Bus :
  les documents qui manquent au total y sont encore (en traitement ou en échec).

  Les noms de comptes et de files sont lus dans les sorties Terraform (infra/) quand ils ne sont pas
  donnés. Demande la CLI Azure connectée à l'abonnement du déploiement (az login), et le rôle
  « Storage Blob Data Reader » sur le compte de stockage.
.EXAMPLE
  ./scripts/compter-sorties.ps1
.EXAMPLE
  ./scripts/compter-sorties.ps1 -Files
.EXAMPLE
  ./scripts/compter-sorties.ps1 -StorageAccount stpseudodevivysdata -Prefix dentaire/ -OutCsv .\echantillon\comptage.csv
#>
param(
    # Compte de stockage « data » (défaut : sortie Terraform data_storage_account_name)
    [string]$StorageAccount,
    [string]$OutputContainer = "pseudonymised",
    [string]$QuarantineContainer = "quarantine",
    # Ne compte que les fichiers dont le chemin commence par ce préfixe (ex. « dentaire/ »)
    [string]$Prefix,
    # Ajoute l'état des files Service Bus (messages actifs et en dead-letter)
    [switch]$Files,
    [string]$ResourceGroup,
    [string]$ServiceBusNamespace,
    # Écrit aussi le décompte dans ce fichier CSV
    [string]$OutCsv
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Get-TerraformOutputs {
    # Sorties Terraform, ou $null si l'état n'est pas accessible depuis ce poste.
    try {
        $json = terraform "-chdir=$root/infra" output "-json" 2>$null
        if ($LASTEXITCODE -or -not $json) { return $null }
        return ($json | Out-String | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-Categorie([string]$Conteneur, [string]$Nom) {
    # Catégorie d'un fichier selon son container et son nom (voir l'aide du script).
    if ($Conteneur -eq $OutputContainer) {
        if ($Nom -match "(^|/)mail-[^/]+/rapport\.json$") { return "1. publié - mail (rapport)" }
        if ($Nom -like "*.rapport.json") { return "1. publié - document (rapport)" }
        if ($Nom -like "*.pseudonymise.txt") { return "3. publié - texte pseudonymisé" }
        if ($Nom -match "(^|/)mail-[^/]+/mail\.json$") { return "3. publié - mail.json" }
        if ($Nom -match "(^|/)mail-[^/]+/body\.(html|txt)$") { return "3. publié - corps de mail" }
        $ext = [IO.Path]::GetExtension($Nom).ToLower()
        return "3. publié - fichier " + $(if ($ext) { $ext } else { "(sans extension)" })
    }
    if ($Nom -match "(^|/)mail-[^/]+/motif\.json$") { return "2. quarantaine - mail (motif)" }
    if ($Nom -like "*.motif.json") { return "2. quarantaine - document (motif)" }
    $ext = [IO.Path]::GetExtension($Nom).ToLower()
    return "4. quarantaine - original " + $(if ($ext) { $ext } else { "(sans extension)" })
}

$outputs = $null
if (-not $StorageAccount -or ($Files -and (-not $ResourceGroup -or -not $ServiceBusNamespace))) {
    $outputs = Get-TerraformOutputs
}
if (-not $StorageAccount) {
    if (-not $outputs) { throw "Indiquez -StorageAccount (sorties Terraform inaccessibles)." }
    $StorageAccount = $outputs.data_storage_account_name.value
}

# ------------------------------------------------------------------ fichiers des deux containers
$categories = New-Object System.Collections.Generic.List[string]
foreach ($conteneur in $OutputContainer, $QuarantineContainer) {
    Write-Host "Liste de $StorageAccount/$conteneur ..."
    $azArgs = @("storage", "blob", "list", "--account-name", $StorageAccount, "--container-name", $conteneur,
              "--auth-mode", "login", "--num-results", "*", "--query", "[].name", "-o", "tsv")
    if ($Prefix) { $azArgs += @("--prefix", $Prefix) }
    $noms = & az @azArgs
    if ($LASTEXITCODE) { throw "az storage blob list a échoué sur $conteneur (az login ? rôle Storage Blob Data Reader ?)" }
    foreach ($nom in $noms) {
        if ($nom) { $categories.Add((Get-Categorie $conteneur $nom)) }
    }
}

$groupes = @($categories | Group-Object | Sort-Object Name | Select-Object Count, Name)
$groupes | Format-Table -AutoSize

$publies = ($groupes | Where-Object Name -like "1.*" | Measure-Object Count -Sum).Sum
$quarantaine = ($groupes | Where-Object Name -like "2.*" | Measure-Object Count -Sum).Sum
$total = [int]$publies + [int]$quarantaine
Write-Host ("Documents publiés        : {0}" -f [int]$publies)
Write-Host ("Documents en quarantaine : {0}" -f [int]$quarantaine)
Write-Host ("Total traités            : {0}" -f $total)
if ($total) {
    Write-Host ("Part en quarantaine      : {0:P1}" -f ([double]$quarantaine / $total))
}

# ------------------------------------------------------------------ files Service Bus
if ($Files) {
    if (-not $ResourceGroup) {
        if (-not $outputs) { throw "Indiquez -ResourceGroup (sorties Terraform inaccessibles)." }
        $ResourceGroup = $outputs.resource_group_name.value
    }
    if (-not $ServiceBusNamespace) {
        if (-not $outputs) { throw "Indiquez -ServiceBusNamespace (sorties Terraform inaccessibles)." }
        $ServiceBusNamespace = $outputs.servicebus_namespace.value
    }
    Write-Host ""
    Write-Host "Files Service Bus ($ServiceBusNamespace) : messages en attente et en dead-letter"
    az servicebus queue list -g $ResourceGroup --namespace-name $ServiceBusNamespace `
        --query "[].{file:name, actifs:countDetails.activeMessageCount, deadletter:countDetails.deadLetterMessageCount}" -o table
    if ($LASTEXITCODE) { throw "az servicebus queue list a échoué" }
}

if ($OutCsv) {
    $lignes = @($groupes) + @(
        [pscustomobject]@{ Count = [int]$publies; Name = "Total documents publiés" },
        [pscustomobject]@{ Count = [int]$quarantaine; Name = "Total documents en quarantaine" },
        [pscustomobject]@{ Count = $total; Name = "Total traités" }
    )
    $lignes | Select-Object @{ n = "Catégorie"; e = { $_.Name } }, @{ n = "Nombre"; e = { $_.Count } } |
        Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Delimiter ";" -Encoding UTF8
    Write-Host "Décompte écrit dans $OutCsv"
}
