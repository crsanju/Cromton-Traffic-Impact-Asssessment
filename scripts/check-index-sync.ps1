Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$staged = @(git diff --cached --name-only --diff-filter=ACMR)
$hasDeveloper = $staged -contains 'index_developer.html'
$hasIndex = $staged -contains 'index.html'

if ($hasIndex) {
  Write-Host 'index.html is staged. Syncing index_formulas.html from index.html...'
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1 -Quiet
  git add index_formulas.html
  Write-Host 'Synced and staged index_formulas.html.'
} elseif ($hasDeveloper) {
  Write-Host 'index_developer.html is staged. No automatic production sync is performed.'
  Write-Host 'Developer file stays isolated until you explicitly promote changes.'
}

exit 0
