param(
  [string]$CardRoot = ""
)

$ErrorActionPreference = "Stop"
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12

function Get-DefaultCardRoot {
  $scriptDirectory = [System.IO.Path]::GetFullPath($PSScriptRoot)
  $parentDirectory = Split-Path -Parent $scriptDirectory
  if (-not [string]::IsNullOrWhiteSpace($parentDirectory) -and (Test-Path -LiteralPath (Join-Path $parentDirectory "media"))) {
    return $parentDirectory
  }

  return $scriptDirectory
}

function Resolve-CardRootPath {
  param([string]$Path)

  $candidate = [string]$Path
  if ([string]::IsNullOrWhiteSpace($candidate)) {
    $candidate = Get-DefaultCardRoot
  }

  $candidate = $candidate.Trim()
  $candidate = $candidate.Trim('"')
  $candidate = $candidate -replace "[\u0000-\u001F]", ""
  $candidate = $candidate -replace "[/\\]+\.$", ""

  $fullPath = [System.IO.Path]::GetFullPath($candidate)
  $rootPath = [System.IO.Path]::GetPathRoot($fullPath)

  if ($fullPath.Length -gt $rootPath.Length) {
    return $fullPath.TrimEnd("\", "/")
  }

  return $fullPath
}

$script:GeneratorName = "Nomad Screen Metadata Builder"
$script:LogPrefix = "NomadScreen"
$script:VideoExtensions = @(".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi")
$script:AudioExtensions = @(".mp3", ".m4a", ".m4b", ".aac", ".wav", ".flac", ".ogg")
$script:ImageExtensions = @(".jpg", ".jpeg", ".png", ".gif", ".webp")
$script:DocumentExtensions = @(".pdf", ".txt", ".md", ".csv", ".gpx", ".kml", ".doc", ".docx")
$script:ToolsPath = [System.IO.Path]::GetFullPath($PSScriptRoot)
$script:RootPath = Resolve-CardRootPath $CardRoot
$script:MediaRoot = Join-Path $script:RootPath "media"
$script:MetadataRoot = Join-Path $script:MediaRoot ".nomadscreen"
$script:PosterRoot = Join-Path $script:MetadataRoot "posters"
$script:BackdropRoot = Join-Path $script:MetadataRoot "backdrops"
$script:MetadataPath = Join-Path $script:MetadataRoot "library.json"
$script:UnmatchedPath = Join-Path $script:MetadataRoot "unmatched.json"
$script:ConfigPath = Join-Path $script:RootPath "nomadscreen.config.json"
$script:LegacyConfigPath = Join-Path $script:RootPath "nomadscreen-metadata.config.json"
$script:TextConfigPath = Join-Path $script:RootPath "nomadscreen-enriched-mode.txt"
$script:MovieSearchCache = @{}
$script:MovieDetailsCache = @{}
$script:ShowSearchCache = @{}
$script:ShowDetailsCache = @{}
$script:EpisodeDetailsCache = @{}
$script:RunStats = [ordered]@{
  apiFailures = 0
  movieMatches = 0
  movieMisses = 0
  showMatches = 0
  showMisses = 0
  episodeMatches = 0
  imageDownloads = 0
  imageCacheHits = 0
  imageSkipped = 0
  staleImagesDeleted = 0
  staleImagesChecked = 0
}

function Write-Info {
  param([string]$Message)
  Write-Host "[$script:LogPrefix] $Message"
}

function Write-Detail {
  param([string]$Message)
  Write-Host "  - $Message"
}

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    New-Item -ItemType Directory -Path $Path | Out-Null
  }
}

function Get-RelativeCardPath {
  param([string]$Path)

  $fullPath = [System.IO.Path]::GetFullPath($Path)
  if ($fullPath.StartsWith($script:RootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    $relative = $fullPath.Substring($script:RootPath.Length).TrimStart("\")
    return "/" + ($relative -replace "\\", "/")
  }

  throw "Path '$Path' is outside the card root."
}

function Get-AbsoluteCardPath {
  param([string]$RelativePath)

  $candidate = [string]$RelativePath
  if ([string]::IsNullOrWhiteSpace($candidate)) {
    return ""
  }

  $candidate = $candidate.Trim()
  $candidate = $candidate.Trim('"')
  $candidate = $candidate.TrimStart("/", "\")
  if ([string]::IsNullOrWhiteSpace($candidate)) {
    return ""
  }

  return [System.IO.Path]::GetFullPath((Join-Path $script:RootPath ($candidate -replace "/", "\")))
}

function Get-NormalizedFullPath {
  param([string]$Path)

  if ([string]::IsNullOrWhiteSpace($Path)) {
    return ""
  }

  $fullPath = [System.IO.Path]::GetFullPath($Path)
  $rootPath = [System.IO.Path]::GetPathRoot($fullPath)
  if ($fullPath.Length -gt $rootPath.Length) {
    return $fullPath.TrimEnd("\", "/").ToLowerInvariant()
  }

  return $fullPath.ToLowerInvariant()
}

function Add-ReferencedGeneratedArtPath {
  param(
    [hashtable]$Map,
    [string]$RelativePath
  )

  $absolutePath = Get-AbsoluteCardPath $RelativePath
  if ([string]::IsNullOrWhiteSpace($absolutePath)) {
    return
  }

  $normalizedPath = Get-NormalizedFullPath $absolutePath
  if ([string]::IsNullOrWhiteSpace($normalizedPath)) {
    return
  }

  $posterRoot = Get-NormalizedFullPath $script:PosterRoot
  $backdropRoot = Get-NormalizedFullPath $script:BackdropRoot
  if ($normalizedPath.StartsWith($posterRoot) -or $normalizedPath.StartsWith($backdropRoot)) {
    $Map[$normalizedPath] = $true
  }
}

function Remove-StaleGeneratedArt {
  param(
    [object[]]$Items,
    [object[]]$Shows
  )

  $referencedPaths = @{}
  foreach ($item in @($Items)) {
    Add-ReferencedGeneratedArtPath -Map $referencedPaths -RelativePath ([string]$item.posterPath)
    Add-ReferencedGeneratedArtPath -Map $referencedPaths -RelativePath ([string]$item.backdropPath)
  }

  foreach ($show in @($Shows)) {
    Add-ReferencedGeneratedArtPath -Map $referencedPaths -RelativePath ([string]$show.posterPath)
    Add-ReferencedGeneratedArtPath -Map $referencedPaths -RelativePath ([string]$show.backdropPath)
  }

  $deletedCount = 0
  $checkedCount = 0
  foreach ($directory in @($script:PosterRoot, $script:BackdropRoot)) {
    if (-not (Test-Path -LiteralPath $directory)) {
      continue
    }

    foreach ($file in @(Get-ChildItem -LiteralPath $directory -File -Recurse)) {
      $extension = $file.Extension.ToLowerInvariant()
      if ($script:ImageExtensions -notcontains $extension) {
        continue
      }

      $checkedCount += 1
      $normalizedPath = Get-NormalizedFullPath $file.FullName
      if ($referencedPaths.ContainsKey($normalizedPath)) {
        continue
      }

      try {
        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
        $deletedCount += 1
        Write-Detail ("Removed stale artwork: {0}" -f (Get-RelativeCardPath $file.FullName))
      } catch {
        try {
          attrib.exe -R -H -S -P $file.FullName 2>$null | Out-Null
        } catch {
        }

        try {
          [System.IO.File]::SetAttributes($file.FullName, [System.IO.FileAttributes]::Archive)
        } catch {
        }

        try {
          Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
          $deletedCount += 1
          Write-Detail ("Removed stale artwork after retry: {0}" -f (Get-RelativeCardPath $file.FullName))
        } catch {
          Write-Warning ("Could not remove stale artwork '{0}': {1}" -f $file.FullName, $_.Exception.Message)
        }
      }
    }
  }

  $script:RunStats.staleImagesDeleted = $deletedCount
  $script:RunStats.staleImagesChecked = $checkedCount
}

function New-DefaultConfigObject {
  return [ordered]@{
    deviceName = "Nomad Screen"
    wifiPassword = "backpackingmedia"
    tmdbApiKey = ""
    tmdbBearerToken = ""
    language = "en-US"
    country = "US"
    downloadImages = $true
    overwriteImages = $false
    minimumMatchScore = 0.55
  }
}

function Get-ConfigValue {
  param(
    [object]$Config,
    [string]$PropertyName
  )

  if ($null -eq $Config) {
    return $null
  }

  $property = $Config.PSObject.Properties[$PropertyName]
  if ($null -eq $property) {
    return $null
  }

  return $property.Value
}

function Merge-ConfigObject {
  param(
    [System.Collections.IDictionary]$Target,
    [object]$Source
  )

  if ($null -eq $Source) {
    return
  }

  foreach ($property in $Source.PSObject.Properties) {
    if ($null -eq $property.Value) {
      continue
    }

    if ($property.Value -is [string]) {
      if (-not [string]::IsNullOrWhiteSpace($property.Value)) {
        $Target[$property.Name] = $property.Value
      }
      continue
    }

    $Target[$property.Name] = $property.Value
  }
}

function Read-JsonConfigFile {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path)) {
    return $null
  }

  $rawConfig = Get-Content -LiteralPath $Path -Raw
  if ([string]::IsNullOrWhiteSpace($rawConfig)) {
    return $null
  }

  return $rawConfig | ConvertFrom-Json
}

function Load-JsonConfig {
  $config = New-DefaultConfigObject
  $legacyConfig = Read-JsonConfigFile $script:LegacyConfigPath
  $primaryConfig = Read-JsonConfigFile $script:ConfigPath

  Merge-ConfigObject -Target $config -Source $legacyConfig
  Merge-ConfigObject -Target $config -Source $primaryConfig

  return [pscustomobject]$config
}

function Convert-TextValueToBoolean {
  param([string]$Value)

  switch ($Value.Trim().ToLowerInvariant()) {
    "1" { return $true }
    "true" { return $true }
    "yes" { return $true }
    "on" { return $true }
    "0" { return $false }
    "false" { return $false }
    "no" { return $false }
    "off" { return $false }
    default { return $null }
  }
}

function Resolve-TextConfigKey {
  param([string]$Key)

  $normalized = ($Key -replace "[^a-zA-Z0-9]", "").ToLowerInvariant()
  switch ($normalized) {
    "tmdbapikey" { return "tmdbApiKey" }
    "apikey" { return "tmdbApiKey" }
    "key" { return "tmdbApiKey" }
    "tmdbbearertoken" { return "tmdbBearerToken" }
    "bearertoken" { return "tmdbBearerToken" }
    "token" { return "tmdbBearerToken" }
    "language" { return "language" }
    "country" { return "country" }
    "downloadimages" { return "downloadImages" }
    "overwriteimages" { return "overwriteImages" }
    "minimummatchscore" { return "minimumMatchScore" }
    "matchscore" { return "minimumMatchScore" }
    default { return "" }
  }
}

function Parse-TextConfigLine {
  param(
    [string]$Line,
    [System.Collections.IDictionary]$Config
  )

  $trimmed = $Line.Trim()
  if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith("#") -or $trimmed.StartsWith(";")) {
    return
  }

  $separatorIndex = $trimmed.IndexOf("=")
  if ($separatorIndex -lt 0) {
    $separatorIndex = $trimmed.IndexOf(":")
  }

  if ($separatorIndex -ge 0) {
    $rawKey = $trimmed.Substring(0, $separatorIndex).Trim()
    $rawValue = $trimmed.Substring($separatorIndex + 1).Trim()
    $resolvedKey = Resolve-TextConfigKey $rawKey
    if ([string]::IsNullOrWhiteSpace($resolvedKey) -or [string]::IsNullOrWhiteSpace($rawValue)) {
      return
    }

    switch ($resolvedKey) {
      "downloadImages" {
        $boolValue = Convert-TextValueToBoolean $rawValue
        if ($null -ne $boolValue) {
          $Config[$resolvedKey] = $boolValue
        }
      }
      "overwriteImages" {
        $boolValue = Convert-TextValueToBoolean $rawValue
        if ($null -ne $boolValue) {
          $Config[$resolvedKey] = $boolValue
        }
      }
      "minimumMatchScore" {
        $number = 0.0
        if ([double]::TryParse($rawValue, [ref]$number)) {
          $Config[$resolvedKey] = $number
        }
      }
      default {
        $Config[$resolvedKey] = $rawValue
      }
    }
    return
  }

  if ([string]::IsNullOrWhiteSpace([string]$Config.tmdbApiKey) -and [string]::IsNullOrWhiteSpace([string]$Config.tmdbBearerToken)) {
    if ($trimmed.Length -gt 48) {
      $Config.tmdbBearerToken = $trimmed
    } else {
      $Config.tmdbApiKey = $trimmed
    }
  }
}

function Load-TextConfig {
  $config = [ordered]@{}

  if (-not (Test-Path -LiteralPath $script:TextConfigPath)) {
    return [pscustomobject]$config
  }

  $lines = Get-Content -LiteralPath $script:TextConfigPath
  foreach ($line in $lines) {
    Parse-TextConfigLine -Line $line -Config $config
  }

  return [pscustomobject]$config
}

$legacyJsonConfig = Read-JsonConfigFile $script:LegacyConfigPath
$primaryJsonConfig = Read-JsonConfigFile $script:ConfigPath
$jsonConfig = Load-JsonConfig
$textConfig = Load-TextConfig
$combinedConfig = New-DefaultConfigObject
Merge-ConfigObject -Target $combinedConfig -Source $jsonConfig
Merge-ConfigObject -Target $combinedConfig -Source $textConfig

$script:Config = [pscustomobject]$combinedConfig
$script:ConfiguredDeviceName = if ([string]::IsNullOrWhiteSpace([string]$script:Config.deviceName)) { "Nomad Screen" } else { [string]$script:Config.deviceName }
$script:LogPrefix = $script:ConfiguredDeviceName
$script:GeneratorName = "$($script:ConfiguredDeviceName) Metadata Builder"
$script:TmdbApiKey = [string]$script:Config.tmdbApiKey
$script:TmdbBearerToken = [string]$script:Config.tmdbBearerToken
$script:TmdbLanguage = if ([string]::IsNullOrWhiteSpace([string]$script:Config.language)) { "en-US" } else { [string]$script:Config.language }
$script:TmdbCountry = if ([string]::IsNullOrWhiteSpace([string]$script:Config.country)) { "US" } else { [string]$script:Config.country }
$script:DownloadImages = if ($null -eq $script:Config.downloadImages) { $true } else { [bool]$script:Config.downloadImages }
$script:OverwriteImages = if ($null -eq $script:Config.overwriteImages) { $false } else { [bool]$script:Config.overwriteImages }
$script:MinimumMatchScore = if ($null -eq $script:Config.minimumMatchScore -or [string]::IsNullOrWhiteSpace([string]$script:Config.minimumMatchScore)) { 0.55 } else { [double]$script:Config.minimumMatchScore }
$script:TmdbEnabled = (-not [string]::IsNullOrWhiteSpace($script:TmdbApiKey)) -or (-not [string]::IsNullOrWhiteSpace($script:TmdbBearerToken))
$textConfigHasCredentials = (-not [string]::IsNullOrWhiteSpace([string](Get-ConfigValue $textConfig "tmdbApiKey"))) -or (-not [string]::IsNullOrWhiteSpace([string](Get-ConfigValue $textConfig "tmdbBearerToken")))
$primaryJsonHasCredentials = (-not [string]::IsNullOrWhiteSpace([string](Get-ConfigValue $primaryJsonConfig "tmdbApiKey"))) -or (-not [string]::IsNullOrWhiteSpace([string](Get-ConfigValue $primaryJsonConfig "tmdbBearerToken")))
$legacyJsonHasCredentials = (-not [string]::IsNullOrWhiteSpace([string](Get-ConfigValue $legacyJsonConfig "tmdbApiKey"))) -or (-not [string]::IsNullOrWhiteSpace([string](Get-ConfigValue $legacyJsonConfig "tmdbBearerToken")))
$script:TmdbCredentialSource =
if ($textConfigHasCredentials) { "nomadscreen-enriched-mode.txt" }
elseif ($primaryJsonHasCredentials) { "nomadscreen.config.json" }
elseif ($legacyJsonHasCredentials) { "nomadscreen-metadata.config.json" }
else { "" }

function Get-MediaType {
  param([string]$Extension)

  $lower = $Extension.ToLowerInvariant()
  if ($script:VideoExtensions -contains $lower) { return "video" }
  if ($script:AudioExtensions -contains $lower) { return "audio" }
  if ($script:ImageExtensions -contains $lower) { return "image" }
  if ($script:DocumentExtensions -contains $lower) { return "document" }
  return ""
}

function Get-MediaSection {
  param([string]$RelativePath)

  $normalized = $RelativePath.ToLowerInvariant()
  if ($normalized.StartsWith("/media/movies/")) { return "movies" }
  if ($normalized.StartsWith("/media/tv/")) { return "tv" }
  if ($normalized.StartsWith("/media/music/")) { return "music" }
  if ($normalized.StartsWith("/media/audiobooks/")) { return "audiobooks" }
  if ($normalized.StartsWith("/media/documents/")) { return "documents" }
  if ($normalized.StartsWith("/media/photos/")) { return "documents" }
  if ($normalized.StartsWith("/media/.nomadscreen/")) { return "metadata" }

  switch (Get-MediaType ([System.IO.Path]::GetExtension($RelativePath))) {
    "video" { return "movies" }
    "audio" { return "music" }
    "image" { return "documents" }
    "document" { return "documents" }
    default { return "library" }
  }
}

function Convert-ToDisplayName {
  param([string]$Text)

  $value = $Text.Trim()
  if ([string]::IsNullOrWhiteSpace($value)) {
    return ""
  }

  $value = $value -replace "[._]+", " "
  $value = $value -replace "\s+", " "
  return $value.Trim()
}

function Get-Slug {
  param([string]$Text)

  $value = $Text.ToLowerInvariant()
  $value = $value -replace "[^a-z0-9]+", "-"
  $value = $value.Trim("-")
  if ([string]::IsNullOrWhiteSpace($value)) {
    return "library-item"
  }
  return $value
}

function Get-YearFromText {
  param([string]$Text)

  $currentYear = (Get-Date).Year + 1
  $matches = [regex]::Matches($Text, "(?<!\d)(19\d{2}|20\d{2}|21\d{2})(?!\d)")
  if ($matches.Count -eq 0) {
    return ""
  }

  for ($index = $matches.Count - 1; $index -ge 0; $index -= 1) {
    $year = [int]$matches[$index].Value
    if ($year -ge 1900 -and $year -le $currentYear) {
      return [string]$year
    }
  }

  return ""
}

function Get-CleanLookupTitle {
  param([string]$Name)

  $value = [System.IO.Path]::GetFileNameWithoutExtension($Name)
  $value = $value -replace "(?i)\b(2160p|1080p|720p|480p|4k|x264|x265|hevc|hdr|hdrip|brrip|bluray|blu-ray|webrip|web-dl|dvdrip|yts|rarbg|proper|repack|aac|dts)\b", " "
  $value = $value -replace "(?i)\b[s]\d{1,2}[e]\d{1,2}\b", " "
  $value = $value -replace "(?i)\b\d{1,2}x\d{1,2}\b", " "
  $value = $value -replace "\[[^\]]*\]", " "
  $value = $value -replace "\((?!\d{4}\))[^)]*\)", " "
  $value = $value -replace "[._]+", " "
  $value = $value -replace "\s+", " "
  return $value.Trim()
}

function Add-UniqueVariant {
  param(
    [System.Collections.Generic.List[string]]$Variants,
    [string]$Value
  )

  $trimmed = [string]$Value
  if ([string]::IsNullOrWhiteSpace($trimmed)) {
    return
  }

  $trimmed = $trimmed.Trim()
  $key = $trimmed.ToLowerInvariant()
  foreach ($existing in $Variants) {
    if ($existing.ToLowerInvariant() -eq $key) {
      return
    }
  }

  $Variants.Add($trimmed) | Out-Null
}

function Get-SearchVariants {
  param(
    [string]$Title,
    [string]$Year
  )

  $variants = New-Object System.Collections.Generic.List[string]
  $results = New-Object System.Collections.Generic.List[string]
  $raw = Convert-ToDisplayName $Title
  $withoutYear = $raw

  if (-not [string]::IsNullOrWhiteSpace($Year)) {
    $escapedYear = [regex]::Escape($Year)
    $withoutYear = $withoutYear -replace "\(($escapedYear)\)", " "
    $withoutYear = $withoutYear -replace "\[($escapedYear)\]", " "
    $withoutYear = $withoutYear -replace "(?<!\d)$escapedYear(?!\d)", " "
    $withoutYear = $withoutYear -replace "\s+", " "
    $withoutYear = $withoutYear.Trim(" ", "-", "_", ".", ",")
  }

  $simplifiedWithoutYear = $withoutYear -replace "[^\p{L}\p{N}\s]", " "
  $simplifiedWithoutYear = $simplifiedWithoutYear -replace "\s+", " "
  $simplifiedWithoutYear = $simplifiedWithoutYear.Trim()

  $simplifiedRaw = $raw -replace "[^\p{L}\p{N}\s]", " "
  $simplifiedRaw = $simplifiedRaw -replace "\s+", " "
  $simplifiedRaw = $simplifiedRaw.Trim()

  Add-UniqueVariant -Variants $variants -Value $withoutYear
  Add-UniqueVariant -Variants $variants -Value $simplifiedWithoutYear
  Add-UniqueVariant -Variants $variants -Value $raw
  Add-UniqueVariant -Variants $variants -Value $simplifiedRaw

  foreach ($variantKey in $variants) {
    $results.Add($variantKey) | Out-Null
  }

  return $results
}

function Get-NormalizedTokens {
  param([string]$Text)

  $clean = Get-CleanLookupTitle $Text
  if ([string]::IsNullOrWhiteSpace($clean)) {
    return @()
  }

  return @(
    $clean.ToLowerInvariant().Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries) |
      ForEach-Object { $_ -replace "[^a-z0-9]", "" } |
      Where-Object { $_.Length -gt 1 } |
      Sort-Object -Unique
  )
}

function Get-TokenSimilarity {
  param(
    [string]$Left,
    [string]$Right
  )

  $leftTokens = Get-NormalizedTokens $Left
  $rightTokens = Get-NormalizedTokens $Right
  if ($leftTokens.Count -eq 0 -or $rightTokens.Count -eq 0) {
    return 0.0
  }

  $shared = @($leftTokens | Where-Object { $rightTokens -contains $_ }).Count
  $union = @($leftTokens + $rightTokens | Sort-Object -Unique).Count
  if ($union -eq 0) {
    return 0.0
  }

  return [math]::Round(($shared / $union), 4)
}

function Get-MatchScore {
  param(
    [string]$LocalTitle,
    [string]$LocalYear,
    [string]$CandidateTitle,
    [string]$CandidateAltTitle,
    [string]$CandidateYear
  )

  $score = [math]::Max(
    (Get-TokenSimilarity $LocalTitle $CandidateTitle),
    (Get-TokenSimilarity $LocalTitle $CandidateAltTitle)
  )

  if ((Get-CleanLookupTitle $LocalTitle).ToLowerInvariant() -eq (Get-CleanLookupTitle $CandidateTitle).ToLowerInvariant()) {
    $score += 0.15
  }

  if (-not [string]::IsNullOrWhiteSpace($LocalYear) -and -not [string]::IsNullOrWhiteSpace($CandidateYear)) {
    $difference = [math]::Abs(([int]$LocalYear) - ([int]$CandidateYear))
    if ($difference -eq 0) {
      $score += 0.2
    } elseif ($difference -eq 1) {
      $score += 0.1
    }
  }

  return [math]::Min([math]::Round($score, 4), 1.0)
}

function Find-TmdbSearchResults {
  param(
    [string]$Path,
    [string]$Label,
    [string]$Title,
    [string]$Year,
    [string]$YearField
  )

  $attemptedKeys = New-Object System.Collections.Generic.List[string]
  $variants = @(Get-SearchVariants -Title $Title -Year $Year)
  if ($variants.Count -eq 0 -and -not [string]::IsNullOrWhiteSpace($Title)) {
    $variants = @($Title.Trim())
  }

  foreach ($variant in $variants) {
    $queries = @()

    if (-not [string]::IsNullOrWhiteSpace($Year)) {
      $queryWithYear = @{
        query = $variant
        include_adult = "false"
        language = $script:TmdbLanguage
      }
      $queryWithYear[$YearField] = $Year
      $queries += [pscustomobject]@{
        Query = $queryWithYear
        Description = ("'{0}' ({1})" -f $variant, $Year)
        Key = "{0}|{1}|year" -f $variant.ToLowerInvariant(), $Year
        UsedYear = $true
      }
    }

    $queries += [pscustomobject]@{
      Query = @{
        query = $variant
        include_adult = "false"
        language = $script:TmdbLanguage
      }
      Description = ("'{0}'" -f $variant)
      Key = "{0}|no-year" -f $variant.ToLowerInvariant()
      UsedYear = $false
    }

    foreach ($attempt in $queries) {
      if ($attemptedKeys.Contains([string]$attempt.Key)) {
        continue
      }

      $attemptedKeys.Add([string]$attempt.Key) | Out-Null
      Write-Detail ("TMDb {0} search: {1}" -f $Label, [string]$attempt.Description)
      $search = Invoke-TmdbApi -Path $Path -Query $attempt.Query
      if ($null -eq $search) {
        return [pscustomobject]@{
          Search = $null
          RequestFailed = $true
        }
      }

      $candidateCount = @($search.results).Count
      if ($candidateCount -gt 0) {
        Write-Detail ("TMDb returned {0} {1} candidate(s)." -f $candidateCount, $Label)
        return [pscustomobject]@{
          Search = $search
          RequestFailed = $false
        }
      }

      if ([bool]$attempt.UsedYear) {
        Write-Detail "No candidates with year filter for this query variant."
      } else {
        Write-Detail "No candidates for this query variant."
      }
    }
  }

  return [pscustomobject]@{
    Search = $null
    RequestFailed = $false
  }
}

function Find-FirstFile {
  param(
    [string[]]$Directories,
    [string[]]$BaseNames
  )

  foreach ($directory in $Directories) {
    if ([string]::IsNullOrWhiteSpace($directory) -or -not (Test-Path -LiteralPath $directory)) {
      continue
    }

    foreach ($baseName in $BaseNames) {
      foreach ($extension in @(".jpg", ".jpeg", ".png", ".webp")) {
        $candidate = Join-Path $directory ($baseName + $extension)
        if (Test-Path -LiteralPath $candidate) {
          return Get-RelativeCardPath $candidate
        }
      }
    }
  }

  return ""
}

function Get-LocalPosterPath {
  param(
    [System.IO.FileInfo]$File,
    [string]$Section
  )

  $fileDirectory = Split-Path -Parent $File.FullName
  $seasonDirectory = $fileDirectory
  $showDirectory = if ($Section -eq "tv") { Split-Path -Parent $seasonDirectory } else { "" }
  $baseName = [System.IO.Path]::GetFileNameWithoutExtension($File.Name)

  return Find-FirstFile -Directories @($fileDirectory, $seasonDirectory, $showDirectory) -BaseNames @($baseName, "poster", "folder", "cover")
}

function Get-LocalBackdropPath {
  param(
    [System.IO.FileInfo]$File,
    [string]$Section
  )

  $fileDirectory = Split-Path -Parent $File.FullName
  $seasonDirectory = $fileDirectory
  $showDirectory = if ($Section -eq "tv") { Split-Path -Parent $seasonDirectory } else { "" }

  return Find-FirstFile -Directories @($fileDirectory, $showDirectory) -BaseNames @("backdrop", "fanart", "background")
}

function Parse-SeasonNumber {
  param([string]$Label)

  if ([string]::IsNullOrWhiteSpace($Label)) {
    return 1
  }

  if ($Label.ToLowerInvariant().StartsWith("special")) {
    return 0
  }

  $digits = [regex]::Match($Label, "(\d+)")
  if ($digits.Success) {
    return [int]$digits.Groups[1].Value
  }

  return 1
}

function Parse-EpisodeNumber {
  param([string]$Name)

  $patterns = @(
    "(?i)s\d{1,2}e(\d{1,3})",
    "(?i)\b\d{1,2}x(\d{1,3})\b",
    "(?i)\bepisode\s*(\d{1,3})\b",
    "^(?:\D*)(\d{1,3})\b"
  )

  foreach ($pattern in $patterns) {
    $match = [regex]::Match($Name, $pattern)
    if ($match.Success) {
      return [int]$match.Groups[1].Value
    }
  }

  return 0
}

function Get-PathParts {
  param([string]$RelativePath)

  return @($RelativePath.Trim("/").Split("/", [System.StringSplitOptions]::RemoveEmptyEntries))
}

function New-BaseItemRecord {
  param([System.IO.FileInfo]$File)

  $relativePath = Get-RelativeCardPath $File.FullName
  $section = Get-MediaSection $relativePath
  $mediaType = Get-MediaType $File.Extension
  $title = Convert-ToDisplayName ([System.IO.Path]::GetFileNameWithoutExtension($File.Name))
  $lookupTitle = Get-CleanLookupTitle $File.Name
  $parts = Get-PathParts $relativePath
  $posterPath = if ($section -eq "documents") { "" } else { Get-LocalPosterPath -File $File -Section $section }
  $backdropPath = if ($section -eq "documents") { "" } else { Get-LocalBackdropPath -File $File -Section $section }

  $record = [ordered]@{
    path = $relativePath
    type = $mediaType
    section = $section
    extension = $File.Extension.TrimStart(".").ToUpperInvariant()
    bytes = [int64]$File.Length
    title = $title
    sortTitle = $title
    overview = ""
    tagline = ""
    year = Get-YearFromText $File.Name
    releaseDate = ""
    genres = ""
    contentRating = ""
    artist = ""
    album = ""
    posterPath = $posterPath
    backdropPath = $backdropPath
    source = "local"
    tmdbRating = 0
    runtimeMinutes = 0
    matchConfidence = 0
    showTitle = ""
    showSlug = ""
    seasonLabel = ""
    seasonNumber = 0
    episodeNumber = 0
    lookupTitle = if ([string]::IsNullOrWhiteSpace($lookupTitle)) { $title } else { $lookupTitle }
  }

  if ($section -eq "tv") {
    $showTitle = if ($parts.Count -ge 3) { Convert-ToDisplayName $parts[2] } else { "Unknown Show" }
    $seasonLabel = if ($parts.Count -ge 4) { Convert-ToDisplayName $parts[3] } else { "Season 1" }
    $record.showTitle = $showTitle
    $record.showSlug = Get-Slug $showTitle
    $record.seasonLabel = $seasonLabel
    $record.seasonNumber = Parse-SeasonNumber $seasonLabel
    $record.episodeNumber = Parse-EpisodeNumber $File.Name
  }

  if ($section -eq "music" -or $section -eq "audiobooks") {
    if ($parts.Count -ge 4) {
      $record.artist = Convert-ToDisplayName $parts[2]
      $record.album = Convert-ToDisplayName $parts[3]
    } elseif ($parts.Count -ge 3) {
      $record.album = Convert-ToDisplayName $parts[2]
    }
  }

  return $record
}

function Merge-RecordValues {
  param(
    [System.Collections.IDictionary]$Target,
    [System.Collections.IDictionary]$Source
  )

  foreach ($key in $Source.Keys) {
    $value = $Source[$key]
    if ($null -eq $value) {
      continue
    }

    if ($value -is [string]) {
      if (-not [string]::IsNullOrWhiteSpace($value)) {
        $Target[$key] = $value
      }
      continue
    }

    if (($value -is [int] -or $value -is [double]) -and [double]$value -gt 0) {
      $Target[$key] = $value
      continue
    }

    if ($value -is [bool]) {
      $Target[$key] = $value
    }
  }
}

function Invoke-TmdbApi {
  param(
    [string]$Path,
    [hashtable]$Query
  )

  if (-not $script:TmdbEnabled) {
    return $null
  }

  $queryParts = @()
  if (-not [string]::IsNullOrWhiteSpace($script:TmdbApiKey)) {
    $queryParts += "api_key=$([uri]::EscapeDataString($script:TmdbApiKey))"
  }

  foreach ($key in $Query.Keys) {
    $value = $Query[$key]
    if ($null -eq $value -or [string]::IsNullOrWhiteSpace([string]$value)) {
      continue
    }
    $queryParts += "{0}={1}" -f [uri]::EscapeDataString($key), [uri]::EscapeDataString([string]$value)
  }

  $uri = "https://api.themoviedb.org/3/$Path"
  if ($queryParts.Count -gt 0) {
    $uri += "?" + ($queryParts -join "&")
  }

  $headers = @{}
  if (-not [string]::IsNullOrWhiteSpace($script:TmdbBearerToken)) {
    $headers.Authorization = "Bearer $($script:TmdbBearerToken)"
  }

  try {
    return Invoke-RestMethod -Uri $uri -Headers $headers -Method Get -TimeoutSec 30
  } catch {
    $script:RunStats.apiFailures += 1
    Write-Warning "TMDb lookup failed for '$Path': $($_.Exception.Message)"
    return $null
  }
}

function Save-TmdbImage {
  param(
    [string]$RemotePath,
    [string]$TargetDirectory,
    [string]$BaseName,
    [string]$Size,
    [string]$Label = "Image"
  )

  if (-not $script:DownloadImages -or [string]::IsNullOrWhiteSpace($RemotePath)) {
    $script:RunStats.imageSkipped += 1
    if (-not $script:DownloadImages) {
      Write-Detail "$Label download disabled by config."
    } else {
      Write-Detail "$Label not available from TMDb."
    }
    return ""
  }

  Ensure-Directory $TargetDirectory
  $extension = [System.IO.Path]::GetExtension($RemotePath)
  if ([string]::IsNullOrWhiteSpace($extension)) {
    $extension = ".jpg"
  }

  $targetPath = Join-Path $TargetDirectory ($BaseName + $extension)
  if ((Test-Path -LiteralPath $targetPath) -and -not $script:OverwriteImages) {
    $script:RunStats.imageCacheHits += 1
    $relativePath = Get-RelativeCardPath $targetPath
    Write-Detail "$Label already cached at $relativePath"
    return $relativePath
  }

  $url = "https://image.tmdb.org/t/p/$Size$RemotePath"
  try {
    Invoke-WebRequest -Uri $url -OutFile $targetPath -UseBasicParsing -TimeoutSec 45 | Out-Null
    $script:RunStats.imageDownloads += 1
    $relativePath = Get-RelativeCardPath $targetPath
    Write-Detail "$Label downloaded to $relativePath"
    return $relativePath
  } catch {
    $script:RunStats.imageSkipped += 1
    Write-Warning "Image download failed for '$url': $($_.Exception.Message)"
    return ""
  }
}

function Get-MovieCertification {
  param([object]$Details)

  foreach ($entry in @($Details.release_dates.results)) {
    if ($entry.iso_3166_1 -ne $script:TmdbCountry) {
      continue
    }

    foreach ($release in @($entry.release_dates)) {
      if (-not [string]::IsNullOrWhiteSpace([string]$release.certification)) {
        return [string]$release.certification
      }
    }
  }

  return ""
}

function Get-ShowCertification {
  param([object]$Details)

  foreach ($entry in @($Details.content_ratings.results)) {
    if ($entry.iso_3166_1 -eq $script:TmdbCountry -and -not [string]::IsNullOrWhiteSpace([string]$entry.rating)) {
      return [string]$entry.rating
    }
  }

  return ""
}

function Get-TmdbMovieDetails {
  param([int]$MovieId)

  if ($script:MovieDetailsCache.ContainsKey($MovieId)) {
    return $script:MovieDetailsCache[$MovieId]
  }

  $details = Invoke-TmdbApi -Path "movie/$MovieId" -Query @{
    language = $script:TmdbLanguage
    append_to_response = "release_dates"
  }
  $script:MovieDetailsCache[$MovieId] = $details
  return $details
}

function Get-TmdbShowDetails {
  param([int]$ShowId)

  if ($script:ShowDetailsCache.ContainsKey($ShowId)) {
    return $script:ShowDetailsCache[$ShowId]
  }

  $details = Invoke-TmdbApi -Path "tv/$ShowId" -Query @{
    language = $script:TmdbLanguage
    append_to_response = "content_ratings"
  }
  $script:ShowDetailsCache[$ShowId] = $details
  return $details
}

function Get-TmdbEpisodeDetails {
  param(
    [int]$ShowId,
    [int]$SeasonNumber,
    [int]$EpisodeNumber
  )

  $key = "$ShowId|$SeasonNumber|$EpisodeNumber"
  if ($script:EpisodeDetailsCache.ContainsKey($key)) {
    return $script:EpisodeDetailsCache[$key]
  }

  $details = Invoke-TmdbApi -Path "tv/$ShowId/season/$SeasonNumber/episode/$EpisodeNumber" -Query @{
    language = $script:TmdbLanguage
  }
  $script:EpisodeDetailsCache[$key] = $details
  return $details
}

function Get-MovieMetadata {
  param([System.Collections.IDictionary]$Item)

  if (-not $script:TmdbEnabled) {
    return $null
  }

  $cacheKey = "$($Item.lookupTitle)|$($Item.year)"
  if ($script:MovieSearchCache.ContainsKey($cacheKey)) {
    Write-Detail "Using cached movie lookup result."
    return $script:MovieSearchCache[$cacheKey]
  }

  $searchResult = Find-TmdbSearchResults -Path "search/movie" -Label "movie" -Title $Item.lookupTitle -Year $Item.year -YearField "year"
  if ($searchResult.RequestFailed) {
    $script:MovieSearchCache[$cacheKey] = $null
    return $null
  }

  $search = $searchResult.Search
  if (($null -eq $search) -or (@($search.results).Count -eq 0)) {
    $script:RunStats.movieMisses += 1
    Write-Detail "No TMDb movie candidates returned."
    $script:MovieSearchCache[$cacheKey] = $null
    return $null
  }

  $bestCandidate = $null
  $bestScore = 0.0
  foreach ($candidate in @($search.results | Select-Object -First 6)) {
    $candidateYear = ""
    if ($candidate.release_date) {
      $candidateYear = ([string]$candidate.release_date).Split("-")[0]
    }
    $score = Get-MatchScore -LocalTitle $Item.lookupTitle -LocalYear $Item.year -CandidateTitle ([string]$candidate.title) -CandidateAltTitle ([string]$candidate.original_title) -CandidateYear $candidateYear
    Write-Detail ("Candidate: {0}{1} | score {2}" -f [string]$candidate.title, $(if ($candidateYear) { " ($candidateYear)" } else { "" }), ([math]::Round($score, 2)))
    if ($score -gt $bestScore) {
      $bestScore = $score
      $bestCandidate = $candidate
    }
  }

  if ($null -eq $bestCandidate -or $bestScore -lt $script:MinimumMatchScore) {
    $script:RunStats.movieMisses += 1
    Write-Detail ("No strong movie match. Best score: {0}" -f ([math]::Round($bestScore, 2)))
    $script:MovieSearchCache[$cacheKey] = $null
    return $null
  }

  Write-Detail ("Best movie match: {0} (score {1})" -f [string]$bestCandidate.title, ([math]::Round($bestScore, 2)))

  $details = Get-TmdbMovieDetails -MovieId ([int]$bestCandidate.id)
  if ($null -eq $details) {
    $script:RunStats.movieMisses += 1
    Write-Detail "Movie details lookup failed."
    $script:MovieSearchCache[$cacheKey] = $null
    return $null
  }

  $metadata = [ordered]@{
    title = [string]$details.title
    sortTitle = [string]$details.title
    year = if ($details.release_date) { ([string]$details.release_date).Split("-")[0] } else { "" }
    releaseDate = [string]$details.release_date
    overview = [string]$details.overview
    tagline = [string]$details.tagline
    genres = (@($details.genres | ForEach-Object { $_.name }) -join ", ")
    contentRating = Get-MovieCertification $details
    tmdbRating = if ($details.vote_average) { [math]::Round([double]$details.vote_average, 1) } else { 0 }
    runtimeMinutes = [double]$details.runtime
    posterPath = Save-TmdbImage -RemotePath ([string]$details.poster_path) -TargetDirectory $script:PosterRoot -BaseName ("movie-{0}" -f $details.id) -Size "w500" -Label "Poster"
    backdropPath = Save-TmdbImage -RemotePath ([string]$details.backdrop_path) -TargetDirectory $script:BackdropRoot -BaseName ("movie-{0}" -f $details.id) -Size "w780" -Label "Backdrop"
    source = "tmdb"
    matchConfidence = [math]::Round($bestScore, 2)
  }

  $script:RunStats.movieMatches += 1
  Write-Detail ("Movie metadata applied: {0} | {1}" -f $metadata.title, $(if ($metadata.year) { $metadata.year } else { "year unknown" }))
  $script:MovieSearchCache[$cacheKey] = $metadata
  return $metadata
}

function Get-ShowMetadata {
  param([System.Collections.IDictionary]$Item)

  if (-not $script:TmdbEnabled) {
    return $null
  }

  $cacheKey = "$($Item.showSlug)|$($Item.showTitle)"
  if ($script:ShowSearchCache.ContainsKey($cacheKey)) {
    Write-Detail "Using cached show lookup result."
    return $script:ShowSearchCache[$cacheKey]
  }

  $searchResult = Find-TmdbSearchResults -Path "search/tv" -Label "show" -Title $Item.showTitle -Year $Item.year -YearField "first_air_date_year"
  if ($searchResult.RequestFailed) {
    $script:ShowSearchCache[$cacheKey] = $null
    return $null
  }

  $search = $searchResult.Search
  if (($null -eq $search) -or (@($search.results).Count -eq 0)) {
    $script:RunStats.showMisses += 1
    Write-Detail "No TMDb show candidates returned."
    $script:ShowSearchCache[$cacheKey] = $null
    return $null
  }

  $bestCandidate = $null
  $bestScore = 0.0
  foreach ($candidate in @($search.results | Select-Object -First 6)) {
    $candidateYear = ""
    if ($candidate.first_air_date) {
      $candidateYear = ([string]$candidate.first_air_date).Split("-")[0]
    }
    $score = Get-MatchScore -LocalTitle $Item.showTitle -LocalYear $Item.year -CandidateTitle ([string]$candidate.name) -CandidateAltTitle ([string]$candidate.original_name) -CandidateYear $candidateYear
    Write-Detail ("Candidate: {0}{1} | score {2}" -f [string]$candidate.name, $(if ($candidateYear) { " ($candidateYear)" } else { "" }), ([math]::Round($score, 2)))
    if ($score -gt $bestScore) {
      $bestScore = $score
      $bestCandidate = $candidate
    }
  }

  if ($null -eq $bestCandidate -or $bestScore -lt $script:MinimumMatchScore) {
    $script:RunStats.showMisses += 1
    Write-Detail ("No strong show match. Best score: {0}" -f ([math]::Round($bestScore, 2)))
    $script:ShowSearchCache[$cacheKey] = $null
    return $null
  }

  Write-Detail ("Best show match: {0} (score {1})" -f [string]$bestCandidate.name, ([math]::Round($bestScore, 2)))

  $details = Get-TmdbShowDetails -ShowId ([int]$bestCandidate.id)
  if ($null -eq $details) {
    $script:RunStats.showMisses += 1
    Write-Detail "Show details lookup failed."
    $script:ShowSearchCache[$cacheKey] = $null
    return $null
  }

  $metadata = [ordered]@{
    slug = Get-Slug ([string]$details.name)
    title = [string]$details.name
    year = if ($details.first_air_date) { ([string]$details.first_air_date).Split("-")[0] } else { "" }
    overview = [string]$details.overview
    genres = (@($details.genres | ForEach-Object { $_.name }) -join ", ")
    contentRating = Get-ShowCertification $details
    tmdbRating = if ($details.vote_average) { [math]::Round([double]$details.vote_average, 1) } else { 0 }
    posterPath = Save-TmdbImage -RemotePath ([string]$details.poster_path) -TargetDirectory $script:PosterRoot -BaseName ("show-{0}" -f $details.id) -Size "w500" -Label "Show poster"
    backdropPath = Save-TmdbImage -RemotePath ([string]$details.backdrop_path) -TargetDirectory $script:BackdropRoot -BaseName ("show-{0}" -f $details.id) -Size "w780" -Label "Show backdrop"
    source = "tmdb"
    matchConfidence = [math]::Round($bestScore, 2)
    tmdbId = [int]$details.id
  }

  $script:RunStats.showMatches += 1
  Write-Detail ("Show metadata applied: {0} | {1}" -f $metadata.title, $(if ($metadata.year) { $metadata.year } else { "year unknown" }))
  $script:ShowSearchCache[$cacheKey] = $metadata
  return $metadata
}

function Get-EpisodeMetadata {
  param(
    [System.Collections.IDictionary]$ShowRecord,
    [System.Collections.IDictionary]$Item
  )

  if (-not $script:TmdbEnabled -or -not $ShowRecord.tmdbId -or $Item.seasonNumber -le 0 -or $Item.episodeNumber -le 0) {
    return $null
  }

  $details = Get-TmdbEpisodeDetails -ShowId ([int]$ShowRecord.tmdbId) -SeasonNumber ([int]$Item.seasonNumber) -EpisodeNumber ([int]$Item.episodeNumber)
  if ($null -eq $details) {
    Write-Detail ("Episode metadata not found for S{0}E{1}" -f $Item.seasonNumber, $Item.episodeNumber)
    return $null
  }

  $script:RunStats.episodeMatches += 1
  Write-Detail ("Episode metadata applied: S{0}E{1} -> {2}" -f $Item.seasonNumber, $Item.episodeNumber, [string]$details.name)
  return [ordered]@{
    title = [string]$details.name
    overview = [string]$details.overview
    releaseDate = [string]$details.air_date
    tmdbRating = if ($details.vote_average) { [math]::Round([double]$details.vote_average, 1) } else { 0 }
    runtimeMinutes = if ($details.runtime) { [double]$details.runtime } else { 0 }
    source = "tmdb"
  }
}

function New-ShowRecord {
  param([System.Collections.IDictionary]$Item)

  return [ordered]@{
    slug = $Item.showSlug
    title = $Item.showTitle
    year = $Item.year
    overview = ""
    genres = ""
    contentRating = ""
    posterPath = $Item.posterPath
    backdropPath = $Item.backdropPath
    source = "local"
    tmdbRating = 0
    matchConfidence = 0
    tmdbId = 0
  }
}

function Project-ShowRecord {
  param([System.Collections.IDictionary]$ShowRecord)

  return [ordered]@{
    slug = $ShowRecord.slug
    title = $ShowRecord.title
    year = $ShowRecord.year
    overview = $ShowRecord.overview
    genres = $ShowRecord.genres
    contentRating = $ShowRecord.contentRating
    posterPath = $ShowRecord.posterPath
    backdropPath = $ShowRecord.backdropPath
    source = $ShowRecord.source
    tmdbRating = $ShowRecord.tmdbRating
    matchConfidence = $ShowRecord.matchConfidence
  }
}

Ensure-Directory $script:MetadataRoot
Ensure-Directory $script:PosterRoot
Ensure-Directory $script:BackdropRoot

if (-not (Test-Path -LiteralPath $script:MediaRoot)) {
  throw "The card is missing the 'media' folder at '$script:MediaRoot'."
}

Write-Info "Scanning media under $script:MediaRoot"
if ($script:TmdbEnabled) {
  Write-Info "TMDb enrichment is enabled."
  if (-not [string]::IsNullOrWhiteSpace($script:TmdbCredentialSource)) {
    Write-Detail "Credentials loaded from $script:TmdbCredentialSource"
  }
  Write-Detail "Language: $script:TmdbLanguage | Country: $script:TmdbCountry | Download images: $script:DownloadImages"
} else {
  Write-Info "TMDb enrichment is disabled. The script will build local metadata only."
  Write-Detail "Edit nomadscreen.config.json in the card root, or use the older nomadscreen-enriched-mode.txt fallback."
}

$mediaFiles = Get-ChildItem -LiteralPath $script:MediaRoot -File -Recurse | Where-Object {
  ($_.FullName -notlike "*\.nomadscreen\*") -and -not [string]::IsNullOrWhiteSpace((Get-MediaType $_.Extension))
}

Write-Info ("Found {0} media file(s) to process." -f $mediaFiles.Count)

$items = New-Object System.Collections.Generic.List[object]
$showMap = @{}
$unmatched = New-Object System.Collections.Generic.List[object]

$currentIndex = 0
foreach ($file in $mediaFiles) {
  $currentIndex += 1
  $item = New-BaseItemRecord -File $file
  if ($item.path -like "/media/.nomadscreen/*") {
    continue
  }

  Write-Info ("[{0}/{1}] {2}" -f $currentIndex, $mediaFiles.Count, $item.path)
  Write-Detail ("Section: {0} | Local title: {1}" -f $item.section, $item.title)
  if ($item.posterPath -or $item.backdropPath) {
    $localArt = @()
    if ($item.posterPath) { $localArt += "poster $($item.posterPath)" }
    if ($item.backdropPath) { $localArt += "backdrop $($item.backdropPath)" }
    Write-Detail ("Local art found: {0}" -f ($localArt -join " | "))
  } else {
    Write-Detail "No local poster/backdrop found."
  }

  switch ($item.section) {
    "movies" {
      $movieMetadata = Get-MovieMetadata -Item $item
      if ($movieMetadata) {
        Merge-RecordValues -Target $item -Source $movieMetadata
      } elseif ($script:TmdbEnabled) {
        Write-Detail "Movie fell back to local-only metadata."
        $unmatched.Add([ordered]@{
          path = $item.path
          section = $item.section
          query = $item.lookupTitle
        }) | Out-Null
      }
    }
    "tv" {
      if (-not $showMap.ContainsKey($item.showSlug)) {
        $showMap[$item.showSlug] = New-ShowRecord -Item $item
      }

      $showRecord = $showMap[$item.showSlug]
      $showMetadata = Get-ShowMetadata -Item $item
      if ($showMetadata) {
        Merge-RecordValues -Target $showRecord -Source $showMetadata
      } elseif ($script:TmdbEnabled -and -not $showRecord.tmdbId) {
        Write-Detail "Show fell back to local-only metadata."
        $unmatched.Add([ordered]@{
          path = $item.path
          section = "tv"
          query = $item.showTitle
        }) | Out-Null
      }

      $item.showTitle = $showRecord.title
      $item.showSlug = $showRecord.slug
      if ([string]::IsNullOrWhiteSpace($item.posterPath)) {
        $item.posterPath = $showRecord.posterPath
      }
      if ([string]::IsNullOrWhiteSpace($item.backdropPath)) {
        $item.backdropPath = $showRecord.backdropPath
      }
      if ([string]::IsNullOrWhiteSpace($item.contentRating)) {
        $item.contentRating = $showRecord.contentRating
      }
      if ([string]::IsNullOrWhiteSpace($item.genres)) {
        $item.genres = $showRecord.genres
      }
      if ([string]::IsNullOrWhiteSpace($item.year)) {
        $item.year = $showRecord.year
      }
      if ($showRecord.tmdbRating -gt 0 -and $item.tmdbRating -le 0) {
        $item.tmdbRating = $showRecord.tmdbRating
      }
      if ($showRecord.matchConfidence -gt 0 -and $item.matchConfidence -le 0) {
        $item.matchConfidence = $showRecord.matchConfidence
      }
      if ($showRecord.source -eq "tmdb") {
        $item.source = "tmdb"
      }

      $episodeMetadata = Get-EpisodeMetadata -ShowRecord $showRecord -Item $item
      if ($episodeMetadata) {
        Merge-RecordValues -Target $item -Source $episodeMetadata
      }
    }
  }

  Write-Detail ("Final title: {0} | Source: {1}" -f $item.title, $item.source)

  $item.Remove("lookupTitle")
  $items.Add($item) | Out-Null
}

$showOutput = @($showMap.Values | ForEach-Object { [pscustomobject](Project-ShowRecord $_) } | Sort-Object title)
$itemOutput = @($items | ForEach-Object { [pscustomobject]$_ })
$unmatchedOutput = @($unmatched | ForEach-Object { [pscustomobject]$_ })
$moviePosterCount = @($itemOutput | Where-Object {
  $_.section -eq "movies" -and -not [string]::IsNullOrWhiteSpace([string]$_.posterPath)
}).Count
$showPosterCount = @($showOutput | Where-Object {
  -not [string]::IsNullOrWhiteSpace([string]$_.posterPath)
}).Count
$episodePosterCount = @($itemOutput | Where-Object {
  $_.section -eq "tv" -and -not [string]::IsNullOrWhiteSpace([string]$_.posterPath)
}).Count
$documentPosterCount = @($itemOutput | Where-Object {
  $_.section -eq "documents" -and -not [string]::IsNullOrWhiteSpace([string]$_.posterPath)
}).Count
$library = [pscustomobject]@{
  version = 1
  generatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  generator = $script:GeneratorName
  shows = $showOutput
  items = $itemOutput
}

$library | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $script:MetadataPath -Encoding UTF8

$unmatchedOutput | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $script:UnmatchedPath -Encoding UTF8

Remove-StaleGeneratedArt -Items $itemOutput -Shows $showOutput

Write-Info ("Metadata written to {0}" -f $script:MetadataPath)
Write-Info ("Indexed {0} item(s) and {1} show record(s)." -f $items.Count, $showOutput.Count)
Write-Info ("Artwork refs: movie posters {0}, show posters {1}, TV item posters {2}, document poster refs {3}" -f $moviePosterCount, $showPosterCount, $episodePosterCount, $documentPosterCount)
Write-Info ("Summary: movie matches {0}, movie misses {1}, show matches {2}, show misses {3}, episode matches {4}" -f $script:RunStats.movieMatches, $script:RunStats.movieMisses, $script:RunStats.showMatches, $script:RunStats.showMisses, $script:RunStats.episodeMatches)
Write-Info ("Images: downloaded {0}, cached {1}, skipped {2}, stale removed {3}/{4} checked | API failures: {5}" -f $script:RunStats.imageDownloads, $script:RunStats.imageCacheHits, $script:RunStats.imageSkipped, $script:RunStats.staleImagesDeleted, $script:RunStats.staleImagesChecked, $script:RunStats.apiFailures)
if ($unmatched.Count -gt 0) {
  Write-Warning ("{0} item(s) did not get a TMDb match. Review {1} if you want to refine titles." -f $unmatched.Count, $script:UnmatchedPath)
}
