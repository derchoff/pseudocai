<#
.SYNOPSIS
  Compte les documents publiés, en quarantaine et en cours de traitement.
.DESCRIPTION
  Lit la liste des fichiers des containers « pseudonymised » et « quarantine » du compte de stockage
  « data » et affiche :

    Documents publiés         PDF hors mail + images hors mail + mails (.eml)
    Documents en quarantaine  PDF hors mail + images hors mail + mails (.eml)
    PDF publiés, images publiées, mails publiés
    Pièces jointes des mails publiés, par type
    PDF, images et mails en quarantaine
    Total traités, part en quarantaine
    En cours : messages en attente et en échec (dead-letter) dans chaque file Service Bus

  Un document déposé compte une fois : un mail compte pour un, quel que soit son nombre de pièces
  jointes ; un JSON de métadonnées de la GED (<document>.json) n'est pas un document (il est compté
  à part). Un dossier de mail est reconnu à son contenu (mail.json, rapport.json, motif.json, .eml),
  quel que soit son nom.

  Les noms du compte de stockage, du groupe de ressources et du namespace Service Bus sont lus dans
  les sorties Terraform (infra/) quand ils ne sont pas donnés. Demande la CLI Azure connectée à
  l'abonnement du déploiement (az login) et le rôle « Storage Blob Data Reader » sur le compte.
.EXAMPLE
  ./scripts/compter-sorties.ps1
.EXAMPLE
  ./scripts/compter-sorties.ps1 -SansFiles -Prefix dentaire/
.EXAMPLE
  ./scripts/compter-sorties.ps1 -StorageAccount stpseudodevivysdata -OutCsv .\echantillon\comptage.csv
#>
param(
    # Compte de stockage « data » (défaut : sortie Terraform data_storage_account_name)
    [string]$StorageAccount,
    [string]$OutputContainer = "pseudonymised",
    [string]$QuarantineContainer = "quarantine",
    # Ne compte que les fichiers dont le chemin commence par ce préfixe (ex. « dentaire/ »)
    [string]$Prefix,
    # N'interroge pas les files Service Bus
    [switch]$SansFiles,
    [string]$ResourceGroup,
    [string]$ServiceBusNamespace,
    # Écrit aussi le décompte dans ce fichier CSV (séparateur ;)
    [string]$OutCsv
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$ImageExtensions = @(".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp", ".gif")

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

function Get-BlobNames([string]$Container) {
    # Chemins de tous les fichiers du container (filtrés par -Prefix).
    Write-Host "Lecture de $StorageAccount/$Container ..."
    $azArgs = @("storage", "blob", "list", "--account-name", $StorageAccount, "--container-name", $Container,
                "--auth-mode", "login", "--num-results", "*", "--query", "[].name", "-o", "tsv")
    if ($Prefix) { $azArgs += @("--prefix", $Prefix) }
    $names = & az @azArgs
    if ($LASTEXITCODE) { throw "az storage blob list a échoué sur $Container (az login ? rôle Storage Blob Data Reader ?)" }
    return @($names | Where-Object { $_ })
}

function Split-BlobPath([string]$Name) {
    # (dossier, nom du fichier) d'un chemin de blob.
    $i = $Name.LastIndexOf("/")
    if ($i -lt 0) { return @("", $Name) }
    return @($Name.Substring(0, $i), $Name.Substring($i + 1))
}

function Get-Kind([string]$FileName) {
    # « pdf », « image », « json » (métadonnées GED) ou « autre » d'après l'extension.
    $ext = [IO.Path]::GetExtension($FileName).ToLower()
    if ($ext -eq ".pdf") { return "pdf" }
    if ($ImageExtensions -contains $ext) { return "image" }
    if ($ext -eq ".json") { return "json" }
    return "autre"
}

function Add-Count([hashtable]$Table, [string]$Key) {
    # Incrémente un compteur.
    if ($Table.ContainsKey($Key)) { $Table[$Key]++ } else { $Table[$Key] = 1 }
}

# ------------------------------------------------------------------ comptes à interroger
$outputs = $null
if (-not $StorageAccount -or (-not $SansFiles -and (-not $ResourceGroup -or -not $ServiceBusNamespace))) {
    $outputs = Get-TerraformOutputs
}
if (-not $StorageAccount) {
    if (-not $outputs) { throw "Indiquez -StorageAccount (sorties Terraform inaccessibles)." }
    $StorageAccount = $outputs.data_storage_account_name.value
}

# ------------------------------------------------------------------ publiés
$published = Get-BlobNames $OutputContainer
# Dossiers de mail : ceux qui contiennent mail.json, rapport.json (sans préfixe) ou body.html|txt.
$mailDirs = New-Object System.Collections.Generic.HashSet[string]
$mailDirsWithReport = New-Object System.Collections.Generic.HashSet[string]
foreach ($name in $published) {
    $dir, $file = Split-BlobPath $name
    if ($file -in @("mail.json", "rapport.json", "body.html", "body.txt")) { [void]$mailDirs.Add($dir) }
    if ($file -eq "rapport.json") { [void]$mailDirsWithReport.Add($dir) }
}
$pub = @{ pdf = 0; image = 0; autre = 0; json = 0 }
$attachments = @{}
$mailJson = 0
foreach ($name in $published) {
    $dir, $file = Split-BlobPath $name
    if ($mailDirs.Contains($dir)) {
        if ($file -in @("mail.json", "rapport.json", "body.html", "body.txt")) { continue }
        if ($file.ToLower().EndsWith(".json")) { $mailJson++; continue }  # métadonnées GED du mail
        $ext = [IO.Path]::GetExtension($file).ToLower()
        Add-Count $attachments $(if ($ext) { $ext } else { "(sans extension)" })
        continue
    }
    if ($file -like "*.rapport.json" -or $file -like "*.pseudonymise.txt") { continue }
    $pub[(Get-Kind $file)]++
}
$pubMails = $mailDirsWithReport.Count
$mailsInProgress = $mailDirs.Count - $pubMails

# ------------------------------------------------------------------ quarantaine
$quarantined = Get-BlobNames $QuarantineContainer
$quarantineMailDirs = New-Object System.Collections.Generic.HashSet[string]
foreach ($name in $quarantined) {
    $dir, $file = Split-BlobPath $name
    if ($file -eq "motif.json") { [void]$quarantineMailDirs.Add($dir) }
}
$qua = @{ pdf = 0; image = 0; autre = 0; json = 0 }
foreach ($name in $quarantined) {
    $dir, $file = Split-BlobPath $name
    if ($quarantineMailDirs.Contains($dir)) { continue }
    if ($file -like "*.motif.json") {
        # Motif d'un document seul : <nom d'origine>.motif.json -> type du document d'origine
        $qua[(Get-Kind $file.Substring(0, $file.Length - ".motif.json".Length))]++
    }
}
$quaMails = $quarantineMailDirs.Count

# ------------------------------------------------------------------ totaux
$totalPub = $pub.pdf + $pub.image + $pub.autre + $pubMails
$totalQua = $qua.pdf + $qua.image + $qua.autre + $quaMails
$total = $totalPub + $totalQua
$rows = New-Object System.Collections.Generic.List[object]
function Add-Row([string]$Label, $Value, [string]$Detail = "") {
    # Ligne du décompte (affichée et écrite dans le CSV).
    $rows.Add([pscustomobject]@{ Indicateur = $Label; Valeur = $Value; Detail = $Detail })
}
$autresPub = if ($pub.autre) { ", autres $($pub.autre)" } else { "" }
$autresQua = if ($qua.autre) { ", autres $($qua.autre)" } else { "" }
Add-Row "Total documents publiés" $totalPub "PDF $($pub.pdf), images $($pub.image), mails $pubMails$autresPub"
Add-Row "Total documents en quarantaine" $totalQua "PDF $($qua.pdf), images $($qua.image), mails $quaMails$autresQua"
$nbAttachments = ($attachments.Values | Measure-Object -Sum).Sum
Add-Row "Pièces jointes des mails publiés" ([int]$nbAttachments) (($attachments.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { "$($_.Key) $($_.Value)" }) -join ", ")
Add-Row "Total traités" $total
Add-Row "Part en quarantaine" $(if ($total) { "{0:P1}" -f ($totalQua / $total) } else { "-" })
if ($pub.json + $mailJson) { Add-Row "JSON de métadonnées GED publiés (non comptés)" ($pub.json + $mailJson) }
if ($qua.json) { Add-Row "JSON de métadonnées GED en quarantaine (non comptés)" $qua.json }
if ($mailsInProgress -gt 0) { Add-Row "Mails en cours de finalisation (dossier sans rapport)" $mailsInProgress }

Write-Host ""
$width = ($rows | ForEach-Object { $_.Indicateur.Length } | Measure-Object -Maximum).Maximum
foreach ($r in $rows) {
    $line = "{0} : {1,8}" -f $r.Indicateur.PadRight($width), $r.Valeur
    if ($r.Detail) { $line += "   ($($r.Detail))" }
    Write-Host $line
}

# ------------------------------------------------------------------ files Service Bus
if (-not $SansFiles) {
    if (-not $ResourceGroup -and $outputs) { $ResourceGroup = $outputs.resource_group_name.value }
    if (-not $ServiceBusNamespace -and $outputs) { $ServiceBusNamespace = $outputs.servicebus_namespace.value }
    Write-Host ""
    if (-not $ResourceGroup -or -not $ServiceBusNamespace) {
        Write-Warning "Files non interrogées : indiquez -ResourceGroup et -ServiceBusNamespace (sorties Terraform inaccessibles)."
    } else {
        $queues = az servicebus queue list -g $ResourceGroup --namespace-name $ServiceBusNamespace `
            --query "[].{file:name, attente:countDetails.activeMessageCount, echec:countDetails.deadLetterMessageCount}" -o json | ConvertFrom-Json
        if ($LASTEXITCODE) { throw "az servicebus queue list a échoué" }
        $enAttente = ($queues | Measure-Object attente -Sum).Sum
        $enEchec = ($queues | Measure-Object echec -Sum).Sum
        Write-Host ("En cours dans les files : {0} en attente ou en traitement, {1} en échec (dead-letter)" -f [int]$enAttente, [int]$enEchec)
        foreach ($q in $queues) {
            Write-Host ("  {0,-16} {1,8} en attente  {2,8} en échec" -f $q.file, $q.attente, $q.echec)
            Add-Row "File $($q.file) : en attente" $q.attente
            Add-Row "File $($q.file) : en échec (dead-letter)" $q.echec
        }
        Write-Host "  (un mail en cours compte plusieurs messages : le mail, puis une tâche par pièce jointe)"
    }
}

if ($OutCsv) {
    $rows | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Delimiter ";" -Encoding UTF8
    Write-Host "Décompte écrit dans $OutCsv"
}
