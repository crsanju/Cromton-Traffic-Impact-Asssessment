param(
  [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$developerPath = Join-Path $repoRoot 'index_developer.html'
$indexPath = Join-Path $repoRoot 'index.html'

if (-not (Test-Path $developerPath)) {
  throw "index_developer.html not found at $developerPath"
}

$developerContent = Get-Content -Path $developerPath -Raw
$outputContent = $developerContent

# Keep production title distinct while mirroring logic/content from index_developer.html.
$outputContent = [regex]::Replace(
  $outputContent,
  '(?is)<title>.*?</title>',
  '<title>Traffic Impact Assessment</title>',
  1
)

$productionBetaHideBlock = @'
/* Production build: beta panel is developer-only. */
#optionalFeaturesSection,
#betaFeaturesCard {
  display: none !important;
}
'@

if ($outputContent -notmatch [regex]::Escape('/* Production build: beta panel is developer-only. */')) {
  $outputContent = [regex]::Replace(
    $outputContent,
    '(?is)(<style[^>]*>\s*)',
    ('$1' + $productionBetaHideBlock + "`r`n"),
    1
  )
}

$existingOutput = if (Test-Path $indexPath) { Get-Content -Path $indexPath -Raw } else { '' }

if ($existingOutput -ne $outputContent) {
  Set-Content -Path $indexPath -Value $outputContent -Encoding UTF8
  if (-not $Quiet) {
    Write-Host 'Synced index.html from index_developer.html (production title preserved).'
  }
} elseif (-not $Quiet) {
  Write-Host 'index.html is already in sync with index_developer.html.'
}
