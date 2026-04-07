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

$formulaEnforcerBlock = @'
<!-- FORMULA MODE ENFORCER START -->
<script>
(function () {
  var enforceScheduled = false;

  function scheduleEnforce() {
    if (enforceScheduled) return;
    enforceScheduled = true;
    requestAnimationFrame(function () {
      enforceScheduled = false;
      enforceFormulaView();
    });
  }

  function enforceFormulaView() {
    if (document.body) {
      document.body.classList.add('formula-mode');
    }

    document.querySelectorAll('.formula-inline').forEach(function (el) {
      el.style.display = 'block';
    });

    document.querySelectorAll('details').forEach(function (d) {
      var text = (d.textContent || '').toLowerCase();
      if (d.id !== 'formulaTraceSection' && (text.indexOf('formula') !== -1 || text.indexOf('step') !== -1)) {
        d.open = true;
      }
    });
  }

  enforceFormulaView();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enforceFormulaView, { once: true });
  }

  var observer = new MutationObserver(function () {
    scheduleEnforce();
  });
  observer.observe(document.documentElement || document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['class', 'style', 'open']
  });

  setInterval(enforceFormulaView, 2000);
})();
</script>
<!-- FORMULA MODE ENFORCER END -->
'@.Trim()

# Keep formula page title distinct while mirroring everything else from index.html.
$outputContent = [regex]::Replace(
  $outputContent,
  '(?is)<title>.*?</title>',
  '<title>Traffic Impact Assessment - Formula Detailed</title>',
  1
)

# Ensure formula page starts in formula-mode immediately on first paint.
$outputContent = $outputContent.Replace(
  '<body class="app-locked input-color-mode">',
  '<body class="app-locked input-color-mode formula-mode">'
)

$formulaBlockPattern = '(?is)<!-- FORMULA MODE ENFORCER START -->.*?<!-- FORMULA MODE ENFORCER END -->'

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
