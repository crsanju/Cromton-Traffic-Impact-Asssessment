Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$staged = @(git diff --cached --name-only --diff-filter=ACMR)
$hasDeveloper = $staged -contains 'index_developer.html'
$hasIndex = $staged -contains 'index.html'

if ($hasDeveloper) {
  Write-Host 'index_developer.html is staged. Running developer-first sync chain...'
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-index-developer.ps1 -Quiet
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1 -Quiet
  git add index.html index_formulas.html
  Write-Host 'Synced and staged index.html + index_formulas.html from index_developer.html.'
} elseif ($hasIndex) {
  Write-Warning 'index.html is staged without index_developer.html. Preferred workflow is to edit index_developer.html first.'
  Write-Host 'Running sync-index-formulas.ps1 for compatibility...'
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1 -Quiet
  git add index_formulas.html
  Write-Host 'Synced and staged index_formulas.html.'
}

exit 0
