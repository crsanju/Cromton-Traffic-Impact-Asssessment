param(
  [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$indexPath = Join-Path $repoRoot 'index.html'
$formulasPath = Join-Path $repoRoot 'index_formulas.html'

if (-not (Test-Path $indexPath)) {
  throw "index.html not found at $indexPath"
}

$indexContent = Get-Content -Path $indexPath -Raw
$outputContent = $indexContent

# Keep formula page title distinct while mirroring everything else from index.html.
$outputContent = [regex]::Replace(
  $outputContent,
  '(?is)<title>.*?</title>',
  '<title>Traffic Impact Assessment - Formula Detailed</title>',
  1
)

$existingFormulasContent = if (Test-Path $formulasPath) {
  Get-Content -Path $formulasPath -Raw
} else {
  ''
}

$formulaBlockPattern = '(?is)<!-- FORMULA MODE ENFORCER START -->.*?<!-- FORMULA MODE ENFORCER END -->'
$formulaBlockMatch = [regex]::Match($existingFormulasContent, $formulaBlockPattern)

if ($formulaBlockMatch.Success) {
  $formulaEnforcerBlock = $formulaBlockMatch.Value.Trim()
} else {
  $formulaEnforcerBlock = @'
<!-- FORMULA MODE ENFORCER START -->
<script>
(function () {
  function enforceFormulaView() {
    if (document.body) {
      document.body.classList.add('formula-mode');
    }

    document.querySelectorAll('.formula-inline').forEach(function (el) {
      el.style.display = 'block';
    });

    var trace = document.getElementById('formulaTraceSection');
    if (trace) {
      trace.style.display = 'block';
      trace.classList.remove('report-section-excluded');
      trace.open = true;
    }

    document.querySelectorAll('details').forEach(function (d) {
      var text = (d.textContent || '').toLowerCase();
      if (d.id === 'formulaTraceSection' || text.indexOf('formula') !== -1 || text.indexOf('step') !== -1) {
        d.open = true;
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enforceFormulaView);
  } else {
    enforceFormulaView();
  }

  setInterval(enforceFormulaView, 300000);
})();
</script>
<!-- FORMULA MODE ENFORCER END -->
'@.Trim()
}

# Remove any pre-existing enforcer block from copied content before re-inserting once.
$outputContent = [regex]::Replace($outputContent, $formulaBlockPattern, '').TrimEnd()

if ($outputContent -notmatch '(?is)</body>') {
  throw 'Could not find </body> tag in index.html while generating index_formulas.html.'
}

$outputContent = [regex]::Replace(
  $outputContent,
  '(?is)</body>',
  "`r`n$formulaEnforcerBlock`r`n</body>",
  1
)

$existingOutput = if (Test-Path $formulasPath) { Get-Content -Path $formulasPath -Raw } else { '' }

if ($existingOutput -ne $outputContent) {
  Set-Content -Path $formulasPath -Value $outputContent -Encoding UTF8
  if (-not $Quiet) {
    Write-Host 'Synced index_formulas.html from index.html (formula mode preserved).'
  }
} elseif (-not $Quiet) {
  Write-Host 'index_formulas.html is already in sync.'
}
