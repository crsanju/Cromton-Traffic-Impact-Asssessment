Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

git config core.hooksPath .githooks
Write-Host 'Configured Git hooks path to .githooks'
