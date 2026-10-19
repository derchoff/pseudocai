<#
.SYNOPSIS
  Copie un échantillon aléatoire et varié de fichiers d'un partage Azure Files vers le container
  d'entrée (incoming) du service de pseudonymisation.

.DESCRIPTION
  1. Liste récursivement les fichiers de la source (azcopy list), ou lit une liste existante (-ListFile).
  2. Garde les types pris en charge : eml, image (jpg, jpeg, png, heic, heif, tif, tiff, webp, bmp), pdf.
     Exclut les noms qui correspondent à -Exclude (expression régulière, facultative).
  3. Regroupe par TYPE DE DOCUMENT (le début du nom, jusqu'au premier « - » : « Acte de deces + Facture
     - 9185575 - 9296581.eml » -> « acte de deces + facture ») ET par type de fichier, puis tire au hasard :
     chaque groupe reçoit d'abord -MinimumParGroupe fichiers (ou tous s'il en a moins), le reste est réparti
     en proportion de la taille des groupes, pour atteindre exactement -Total fichiers.
  4. Ajoute à la copie le JSON de métadonnées de chaque document tiré (<nom du document>.json, ex.
     « facture.pdf.json »), s'il existe dans la source. Les JSON ne comptent pas dans -Total ni dans
     les groupes : ce sont les documents qui sont tirés ; -NoMetadata les laisse de côté.
  5. Écrit l'échantillon (echantillon.txt pour AzCopy, echantillon.csv pour la traçabilité), affiche la
     répartition, demande confirmation, puis copie avec azcopy copy --list-of-files (copie côté serveur,
     arborescence conservée sous la destination).

  Chaque fichier copié dans incoming déclenche un traitement complet (OCR, agents IA) : commencer par un
  petit -Total pour valider le coût et le taux de quarantaine.

.PARAMETER Source
  Dossier source sur Azure Files : https://<compte>.file.core.windows.net/<partage>/<dossier>
.PARAMETER Destination
  Container (et dossier facultatif) de destination : https://<compte>.blob.core.windows.net/incoming[/<dossier>]
.PARAMETER SourceSas
  Jeton SAS de la source (lecture + liste), avec ou sans « ? ». Vide : connexion azcopy login.
.PARAMETER DestinationSas
  Jeton SAS de la destination (écriture). Vide : connexion azcopy login.
.PARAMETER AutoLogin
  Connexion d'AzCopy quand un jeton SAS manque, SANS question interactive (variable AZCOPY_AUTO_LOGIN_TYPE) :
  AZCLI (session « az login » de la CLI Azure), DEVICE (code à saisir sur https://microsoft.com/devicelogin),
  PSCRED (session Connect-AzAccount), ou vide pour utiliser une session « azcopy login » ouverte avant.
.PARAMETER TenantId
  Tenant Entra ID de la connexion (variable AZCOPY_TENANT_ID). Vide : tenant par défaut de la session.
.PARAMETER Total
  Nombre de fichiers à copier (10 000 par défaut).
.PARAMETER MinimumParGroupe
  Nombre minimal de fichiers par groupe (type de document × type de fichier), pour que chaque groupe soit représenté.
.PARAMETER GroupBy
  Complet (défaut) : type de document entier (« acte de deces + facture + modification »).
  Principal : partie avant le premier « + » (« acte de deces ») ; moins de groupes.
  Un nom sans « - » ou dont le début ne contient aucune lettre (« 9613781 - 9724812.pdf ») va dans
  le groupe « (sans type) ».
.PARAMETER Seed
  Graine du tirage. Absente : tirée au hasard et affichée, pour pouvoir refaire le même échantillon.
.PARAMETER Exclude
  Expression régulière sur le NOM du fichier ; les fichiers qui correspondent sont écartés.
  Ex. '(?i)d[ée]compte' écarte decompte, Décompte, DECOMPTE.
.PARAMETER ListFile
  Liste de fichiers déjà obtenue (un chemin relatif à -Source par ligne) : évite de relister la source.
.PARAMETER OutDir
  Dossier où écrire la liste des fichiers de la source, l'échantillon et sa répartition.
.PARAMETER ProgressEvery
  Rafraîchit les compteurs tous les N fichiers (10 par défaut ; au plus 4 fois par seconde, car
  l'affichage est lent sous Windows PowerShell 5.1).
.PARAMETER NoEstimate
  N'essaie pas d'estimer le nombre de fichiers avant la liste (métrique Azure Monitor « FileCount »).
.PARAMETER DryRun
  Passe --dry-run à AzCopy : affiche ce qui serait copié, sans rien copier.
.PARAMETER SampleOnly
  S'arrête après l'écriture de l'échantillon (aucun appel à azcopy copy).
.PARAMETER NoMetadata
  Ne copie pas les JSON de métadonnées (<nom du document>.json) des documents tirés.
.PARAMETER NoOverwrite
  Ne recopie pas un fichier déjà présent dans la destination (--overwrite=false) : relancer le même
  tirage ne déclenche pas un second traitement des fichiers déjà copiés.
.PARAMETER Force
  Ne demande pas de confirmation avant la copie.

.EXAMPLE
  ./scripts/copy-echantillon.ps1 -SourceSas "?sv=..." -DryRun
.EXAMPLE
  ./scripts/copy-echantillon.ps1 -AutoLogin AZCLI -TenantId 87403574-1718-4e11-9d35-b91a4ba49fa9 -SampleOnly
.EXAMPLE
  ./scripts/copy-echantillon.ps1 -Total 200 -Exclude '(?i)d[ée]compte' -Seed 42
.EXAMPLE
  ./scripts/copy-echantillon.ps1 -ListFile .\echantillon\source.txt -SampleOnly -GroupBy Principal -OutDir .\echantillon-principal
.EXAMPLE
  ./scripts/copy-echantillon.ps1 -SourceSas "?sv=..." -MinimumParGroupe 200 -SampleOnly
.LINK
  docs/echantillonnage.md
.EXAMPLE
  ./scripts/copy-echantillon.ps1 -Source "https://autre.file.core.windows.net/partage/dossier" -Destination "https://stpseudodevivysdata.blob.core.windows.net/incoming/lot2"
#>
param(
    [string]$Source = "https://corpusdocuments.file.core.windows.net/corpusdocumentsshare/qualification_sante",
    [string]$Destination = "https://stpseudodevivysdata.blob.core.windows.net/incoming",
    [string]$SourceSas = "",
    [string]$DestinationSas = "",
    [ValidateSet("AZCLI", "DEVICE", "PSCRED", "")][string]$AutoLogin = "AZCLI",
    [string]$TenantId = "",
    [ValidateRange(1, 1000000)][int]$Total = 10000,
    [ValidateRange(0, 100000)][int]$MinimumParGroupe = 5,
    [ValidateSet("Complet", "Principal")][string]$GroupBy = "Complet",
    [Nullable[int]]$Seed = $null,
    [string]$Exclude = "",
    [string]$ListFile = "",
    [string]$OutDir = ".\echantillon",
    [ValidateRange(1, 1000000)][int]$ProgressEvery = 10,
    [switch]$NoEstimate,
    [switch]$DryRun,
    [switch]$SampleOnly,
    [switch]$NoMetadata,
    [switch]$NoOverwrite,
    [switch]$Force
)
$ErrorActionPreference = "Stop"

$ImageExtensions = @(".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp")

function Get-FileType([string]$Path) {
    # Type d'un fichier d'après son extension : eml, image, pdf, ou $null (non pris en charge).
    $ext = [System.IO.Path]::GetExtension($Path).ToLowerInvariant()
    if ($ext -eq ".eml") { return "eml" }
    if ($ext -eq ".pdf") { return "pdf" }
    if ($ImageExtensions -contains $ext) { return "image" }
    return $null
}

function Get-DocType([string]$Path) {
    # Type de document : début du nom de fichier jusqu'au premier « - » (en minuscules, espaces réduits).
    # Avec -GroupBy Principal : seulement la partie avant le premier « + ».
    $name = ($Path -split "/")[-1]
    $i = $name.IndexOf(" - ")
    if ($i -lt 0) { return "(sans type)" }
    $type = $name.Substring(0, $i)
    if ($GroupBy -eq "Principal") { $type = ($type -split "\s*\+\s*")[0] }
    $type = ($type -replace "\s+", " ").Trim()
    if ($type -notmatch "\p{L}") { return "(sans type)" }
    return $type.ToLowerInvariant()
}

function Get-Shuffled([object[]]$Items, [System.Random]$Rng) {
    # Mélange de Fisher-Yates (reproductible avec la même graine).
    $a = @($Items)
    for ($i = $a.Count - 1; $i -gt 0; $i--) {
        $j = $Rng.Next($i + 1)
        $tmp = $a[$i]; $a[$i] = $a[$j]; $a[$j] = $tmp
    }
    return ,$a
}

$script:LastDraw = [DateTime]::MinValue

function Show-Progress([int]$Id, [string]$Activity, [string]$Status, [int]$Done, [int]$Of, [string]$Operation = "") {
    # Barre de progression rafraîchie tous les -ProgressEvery éléments, au plus 4 fois par seconde.
    if ($Done % $ProgressEvery -ne 0) { return }
    $now = [DateTime]::UtcNow
    if (($now - $script:LastDraw).TotalMilliseconds -lt 250) { return }
    $script:LastDraw = $now
    $params = @{ Id = $Id; Activity = $Activity; Status = $Status }
    if ($Of -gt 0) { $params.PercentComplete = [math]::Min(100, [int](100 * $Done / $Of)) }
    if ($Operation) { $params.CurrentOperation = $Operation }
    Write-Progress @params
}

function Get-FileCountEstimate {
    # Estimation INDICATIVE du nombre de fichiers avant la liste : métrique Azure Monitor « FileCount »
    # du partage ENTIER (fichiers .json compris, tous dossiers), mise à jour environ toutes les heures.
    # Demande la CLI Azure connectée à l'abonnement du compte source. $null si indisponible.
    if ($NoEstimate -or -not (Get-Command az -ErrorAction SilentlyContinue)) { return $null }
    if ($Source -notmatch "^https://([^.]+)\.file\.core\.windows\.net/([^/?]+)") { return $null }
    $account = $Matches[1]; $share = $Matches[2]
    try {
        $id = az storage account show --name $account --query id -o tsv 2>$null
        if (-not $id) { return $null }
        $start = (Get-Date).ToUniversalTime().AddHours(-24).ToString("yyyy-MM-ddTHH:mm:ssZ")
        $value = az monitor metrics list --resource "$id/fileServices/default" --metric FileCount `
            --aggregation Average --interval PT1H --start-time $start --filter "FileShare eq '$share'" `
            --query "value[0].timeseries[0].data[?average!=null].average | [-1]" -o tsv 2>$null
        if ($value) { return [int][double]$value }
    } catch { }
    return $null
}

function Join-Sas([string]$Url, [string]$Sas) {
    # URL + jeton SAS (avec ou sans « ? »).
    if (-not $Sas) { return $Url }
    return $Url.TrimEnd("/") + "?" + $Sas.TrimStart("?")
}

# ------------------------------------------------------------------ contrôles
if ($Source -notmatch "^https://[^/]+\.file\.core\.windows\.net/[^/]+") {
    throw "Source : URL Azure Files attendue (https://<compte>.file.core.windows.net/<partage>/...)."
}
if ($Destination -notmatch "^https://[^/]+\.blob\.core\.windows\.net/[^/]+") {
    throw "Destination : URL Blob attendue (https://<compte>.blob.core.windows.net/<container>/...)."
}
if (-not $SampleOnly -or -not $ListFile) {
    if (-not (Get-Command azcopy -ErrorAction SilentlyContinue)) {
        throw "azcopy introuvable : winget install --id Microsoft.Azure.AZCopy.10 -e, puis rouvrir le terminal."
    }
}
# Connexion sans question interactive : sinon AzCopy attend une réponse (« Reauthorize and continue? »)
# que le script ne peut pas donner, et reste bloqué.
if ((-not $SourceSas -or -not $DestinationSas) -and $AutoLogin) {
    $env:AZCOPY_AUTO_LOGIN_TYPE = $AutoLogin
    if ($TenantId) { $env:AZCOPY_TENANT_ID = $TenantId }
    if ($AutoLogin -eq "AZCLI") {
        $account = az account show --query "{tenant:tenantId, compte:user.name, abonnement:name}" -o json 2>$null | ConvertFrom-Json
        if (-not $account) { throw "AutoLogin AZCLI : aucune session « az login » (lancer az login --tenant <tenant>)." }
        Write-Host "AzCopy utilise la session az login : $($account.compte), tenant $($account.tenant) ($($account.abonnement))." -ForegroundColor Cyan
        if ($TenantId -and $account.tenant -ne $TenantId) {
            throw "La session az login est sur le tenant $($account.tenant), pas sur $TenantId : az login --tenant $TenantId"
        }
    }
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

# ------------------------------------------------------------------ 1. liste des fichiers
if ($ListFile) {
    $all = @(Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_.Trim() -ne "" })
    Write-Host "Liste lue dans $ListFile : $($all.Count) fichier(s)."
} else {
    $estimate = Get-FileCountEstimate
    if ($estimate) {
        Write-Host "Estimation : environ $estimate fichier(s) dans tout le partage (métrique Azure Monitor, indicative : .json et autres dossiers compris)."
    } else {
        Write-Host "Nombre total inconnu avant la liste : le compteur avance sans pourcentage."
    }
    Write-Host "Liste des fichiers de $Source ..."
    # Sortie d'AzCopy traitée au fil de l'eau : le compteur avance pendant la liste.
    $found = New-Object System.Collections.Generic.List[string]
    $firstLines = New-Object System.Collections.Generic.List[string]
    $lineCount = 0
    # AzCopy écrit en UTF-8, mais Windows PowerShell 5.1 lit la sortie des programmes avec
    # l'encodage de la console (page de code 850 en France) : « remboursés » deviendrait
    # « rembours├®s », un fichier introuvable (404) à la copie. Lecture forcée en UTF-8.
    $consoleEncoding = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
    try {
    & azcopy list (Join-Sas $Source $SourceSas) | ForEach-Object {
        $line = [string]$_
        $lineCount++
        if ($firstLines.Count -lt 5) { $firstLines.Add($line) }
        # « INFO: » en tête de ligne selon la version d'AzCopy
        if ($line -match "^(?:INFO:\s*)?(.+);\s+Content Length:") {
            $name = $Matches[1].Trim()
            if ($name -and $name -notmatch "/$") {
                $found.Add($name)
                $status = if ($estimate) { "$($found.Count) / ~$estimate fichier(s) listé(s)" } else { "$($found.Count) fichier(s) listé(s)" }
                Show-Progress -Id 1 -Activity "Liste de la source" -Status $status -Done $found.Count -Of ([int]$estimate)
            }
        }
    }
    $code = $LASTEXITCODE
    } finally {
        [Console]::OutputEncoding = $consoleEncoding
    }
    Write-Progress -Id 1 -Activity "Liste de la source" -Completed
    if ($code) {
        throw "azcopy list a échoué (droits ? jeton SAS ? azcopy login ?). Premières lignes :`n$($firstLines -join "`n")"
    }
    if (-not $found.Count -and $lineCount) {
        throw "Aucun fichier reconnu dans la sortie d'azcopy list. Premières lignes :`n$($firstLines -join "`n")"
    }
    $all = $found.ToArray()
    [System.IO.File]::WriteAllLines((Join-Path $OutDir "source.txt"), [string[]]$all)
    Write-Host "$($all.Count) fichier(s) listé(s) ; liste enregistrée dans $OutDir\source.txt (réutilisable avec -ListFile)."
}

# Index de tous les fichiers listés (casse ignorée), pour retrouver le JSON de métadonnées d'un
# document : « Acte de deces - 9133996 - 9244997.eml » -> « Acte de deces - 9133996 - 9244997.eml.json ».
$listed = New-Object "System.Collections.Generic.Dictionary[string,string]" ([System.StringComparer]::OrdinalIgnoreCase)
foreach ($path in $all) { if (-not $listed.ContainsKey($path)) { $listed[$path] = $path } }

# ------------------------------------------------------------------ 2. filtres et groupes
# HashSet et dictionnaire : Select-Object -Unique et Group-Object deviennent très lents sous Windows
# PowerShell 5.1 sur des centaines de milliers de fichiers.
$seen = New-Object System.Collections.Generic.HashSet[string]
$byGroup = New-Object "System.Collections.Generic.Dictionary[string,System.Collections.Generic.List[object]]"
$kept = 0; $i = 0; $count = $all.Count
foreach ($path in $all) {
    $i++
    Show-Progress -Id 2 -Activity "Filtre et classement" -Status "$i / $count fichier(s) examiné(s), $kept retenu(s)" -Done $i -Of $count
    if (-not $seen.Add($path)) { continue }  # une liste fournie peut contenir des doublons
    $type = Get-FileType $path
    if (-not $type) { continue }
    $name = ($path -split "/")[-1]
    if ($Exclude -and $name -match $Exclude) { continue }
    $row = [pscustomobject]@{ Chemin = $path; TypeDocument = (Get-DocType $path); Type = $type }
    $key = "$type|$($row.TypeDocument)"
    if (-not $byGroup.ContainsKey($key)) { $byGroup[$key] = New-Object System.Collections.Generic.List[object] }
    $byGroup[$key].Add($row)
    $kept++
}
Write-Progress -Id 2 -Activity "Filtre et classement" -Completed
$ignored = $count - $kept
Write-Host "$kept fichier(s) retenu(s) ($ignored écarté(s) : type non pris en charge, exclusion ou doublon)."
if (-not $kept) { throw "Aucun fichier à échantillonner." }

# ------------------------------------------------------------------ 3. tirage stratifié
if ($null -eq $Seed) { $Seed = Get-Random -Minimum 1 -Maximum 2147483647 }
$rng = New-Object System.Random ([int]$Seed)
$target = [math]::Min($Total, $kept)

$groups = @(foreach ($key in ($byGroup.Keys | Sort-Object)) {
    $items = $byGroup[$key].ToArray()
    [pscustomobject]@{
        Type = $items[0].Type; TypeDocument = $items[0].TypeDocument; Disponibles = $items.Count
        Fichiers = (Get-Shuffled -Items $items -Rng $rng); Retenus = 0
    }
})

# a. minimum garanti par groupe (si le total le permet ; sinon 1 par groupe, les plus gros d'abord)
$minimum = $MinimumParGroupe
while ($minimum -gt 0 -and (($groups | ForEach-Object { [math]::Min($_.Disponibles, $minimum) } | Measure-Object -Sum).Sum -gt $target)) {
    $minimum--
}
foreach ($g in $groups) { $g.Retenus = [math]::Min($g.Disponibles, $minimum) }
if ($minimum -eq 0) {
    foreach ($g in ($groups | Sort-Object Disponibles -Descending | Select-Object -First $target)) { $g.Retenus = 1 }
}

# b. reste réparti en proportion de la place restante dans chaque groupe (plus forts restes)
$remaining = $target - ($groups | Measure-Object -Property Retenus -Sum).Sum
$capacity = ($groups | ForEach-Object { $_.Disponibles - $_.Retenus } | Measure-Object -Sum).Sum
if ($remaining -gt 0 -and $capacity -gt 0) {
    $shares = foreach ($g in $groups) {
        $exact = $remaining * ($g.Disponibles - $g.Retenus) / $capacity
        [pscustomobject]@{ Groupe = $g; Part = [int][math]::Floor($exact); Reste = $exact - [math]::Floor($exact) }
    }
    foreach ($s in $shares) { $s.Groupe.Retenus += $s.Part }
    $left = $remaining - ($shares | Measure-Object -Property Part -Sum).Sum
    foreach ($s in ($shares | Sort-Object Reste -Descending)) {
        if ($left -le 0) { break }
        if ($s.Groupe.Retenus -lt $s.Groupe.Disponibles) { $s.Groupe.Retenus++; $left-- }
    }
}

# c. tirage : compteurs par type de fichier et restant à tirer
$sample = New-Object System.Collections.Generic.List[object]
$counts = @{ eml = 0; image = 0; pdf = 0 }
$n = 0
foreach ($g in $groups) {
    $n++
    $operation = "Groupe $n / $($groups.Count) : $($g.TypeDocument) ($($g.Type)), $($g.Retenus) sur $($g.Disponibles)"
    for ($k = 0; $k -lt $g.Retenus; $k++) {
        $f = $g.Fichiers[$k]
        $sample.Add($f)
        $counts[$f.Type]++
        $status = "{0} / {1} tiré(s) - eml {2}, image {3}, pdf {4} - reste {5}" -f `
            $sample.Count, $target, $counts["eml"], $counts["image"], $counts["pdf"], ($target - $sample.Count)
        Show-Progress -Id 3 -Activity "Tirage de l'échantillon" -Status $status -Done $sample.Count -Of $target -Operation $operation
    }
}
Write-Progress -Id 3 -Activity "Tirage de l'échantillon" -Completed
$sample = $sample.ToArray()

# d. JSON de métadonnées des documents tirés (hors total) : copiés avec leur document.
$metadata = New-Object System.Collections.Generic.List[string]
foreach ($f in $sample) {
    $json = $null
    if (-not $NoMetadata -and $listed.ContainsKey("$($f.Chemin).json")) {
        $json = $listed["$($f.Chemin).json"]
        $metadata.Add($json)
    }
    $f | Add-Member -NotePropertyName Metadonnees -NotePropertyValue $json -Force
}

# ------------------------------------------------------------------ 4. échantillon et copie
$listPath = Join-Path $OutDir "echantillon.txt"
$toCopy = [string[]](@($sample | ForEach-Object { $_.Chemin }) + @($metadata))
[System.IO.File]::WriteAllLines($listPath, $toCopy)  # UTF-8 sans BOM
$sample | Export-Csv -Path (Join-Path $OutDir "echantillon.csv") -NoTypeInformation -Encoding UTF8
$groups | Select-Object TypeDocument, Type, Disponibles, Retenus |
    Export-Csv -Path (Join-Path $OutDir "repartition.csv") -NoTypeInformation -Encoding UTF8

Write-Host ""
Write-Host "Répartition par type :"
$sample | Group-Object Type | Sort-Object Name | ForEach-Object { Write-Host ("  {0,-6} {1,7}" -f $_.Name, $_.Count) }
Write-Host "Répartition par type de document :"
$sample | Group-Object TypeDocument | Sort-Object Count -Descending | Select-Object -First 25 |
    ForEach-Object { Write-Host ("  {0,7}  {1}" -f $_.Count, $_.Name) }
Write-Host "Groupes (type de document x type de fichier) : $($groups.Count) ; détail dans repartition.csv"
Write-Host "Échantillon : $($sample.Count) document(s), graine $Seed (refaire le même tirage : -Seed $Seed)."
if ($NoMetadata) {
    Write-Host "JSON de métadonnées : non copiés (-NoMetadata)."
} else {
    Write-Host "JSON de métadonnées : $($metadata.Count) document(s) en ont un, $($sample.Count - $metadata.Count) sans ; $($toCopy.Count) fichier(s) à copier au total."
}
Write-Host "Fichiers : $listPath, echantillon.csv, repartition.csv"

if ($SampleOnly) { exit 0 }

if (-not $Force -and -not $DryRun) {
    Write-Host ""
    Write-Host "Chaque fichier copié dans la destination déclenche un traitement complet (OCR, agents IA)." -ForegroundColor Yellow
    $answer = Read-Host "Copier $($sample.Count) document(s) et $($metadata.Count) JSON de métadonnées vers $Destination ? (oui/non)"
    if ($answer -ne "oui") { Write-Host "Abandon."; exit 1 }
}

$copyArgs = @("copy", (Join-Sas $Source $SourceSas), (Join-Sas $Destination $DestinationSas), "--list-of-files=$listPath")
if ($DryRun) { $copyArgs += "--dry-run" }
if ($NoOverwrite) { $copyArgs += "--overwrite=false" }
& azcopy @copyArgs
if ($LASTEXITCODE) { throw "azcopy copy a échoué (code $LASTEXITCODE). Reprise possible : azcopy jobs resume <id>." }
Write-Host "Terminé."
