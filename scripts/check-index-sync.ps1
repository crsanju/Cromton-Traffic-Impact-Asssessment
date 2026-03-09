Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$staged = @(git diff --cached --name-only --diff-filter=ACMR)
$hasIndex = $staged -contains 'index.html'

if ($hasIndex) {
  Write-Host 'index.html is staged. Running sync-index-formulas.ps1...'
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1 -Quiet
  git add index_formulas.html
  Write-Host 'Synced and staged index_formulas.html.'
}

exit 0
