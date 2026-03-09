(function () {
  'use strict';

  function safeNumber(value, fallback) {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  }

  function setFormula(targetId, text) {
    if (typeof window.setFormulaBelow === 'function') {
      try { window.setFormulaBelow(targetId, text); } catch (_) {}
    }
  }

  function refreshFormulaAnnotations() {
    if (typeof window.applyGlobalFormulaAnnotations === 'function') {
      try { window.applyGlobalFormulaAnnotations(); } catch (_) {}
    }
  }

  function validateAGTTMGeometry() {
    const laneEl = document.getElementById('laneWidth');
    const roadClassEl = document.getElementById('roadClass');
    const badge = document.getElementById('laneStatusBadge');
    if (!laneEl || !roadClassEl || !badge) return;

    const laneW = parseFloat(laneEl.value);
    const roadType = String(roadClassEl.value || 'urban_arterial');

    let minThreshold = 3.3;
    if (roadType === 'urban_local') minThreshold = 3.0;
    if (roadType === 'freeway') minThreshold = 3.5;

    if (Number.isFinite(laneW) && laneW < minThreshold) {
      badge.innerText = 'DEPARTURE FROM STANDARD';
      badge.style.background = '#fee2e2';
      badge.style.color = '#b91c1c';
    } else {
      badge.innerText = 'AGTTM COMPLIANT';
      badge.style.background = '#dcfce7';
      badge.style.color = '#166534';
    }
  }

  function updateGeometryDefaults() {
    const roadClassEl = document.getElementById('roadClass');
    const speedEl = document.getElementById('agttmSpeed');
    const laneWidthEl = document.getElementById('laneWidth');
    const shoulderEl = document.getElementById('shoulderWidth');
    const offsetEl = document.getElementById('lateralOffset');
    if (!roadClassEl) return;

    const roadClass = String(roadClassEl.value || 'urban_arterial');
    const defaults = {
      urban_arterial: { speed: 60, lane: 3.5, shoulder: 1.0, offset: 0.6 },
      urban_local: { speed: 50, lane: 3.1, shoulder: 0.5, offset: 0.3 },
      rural_highway: { speed: 100, lane: 3.5, shoulder: 2.0, offset: 1.0 },
      freeway: { speed: 110, lane: 3.5, shoulder: 2.5, offset: 1.2 }
    };
    const selected = defaults[roadClass] || defaults.urban_arterial;

    if (speedEl) speedEl.value = String(selected.speed);
    if (laneWidthEl) laneWidthEl.value = Number(selected.lane).toFixed(1);
    if (shoulderEl) shoulderEl.value = Number(selected.shoulder).toFixed(1);
    if (offsetEl) offsetEl.value = Number(selected.offset).toFixed(1);

    validateAGTTMGeometry();
    calculateAgttmGeometry();
  }

  function calculateAgttmGeometry() {
    const speedEl = document.getElementById('agttmSpeed');
    const laneWidthEl = document.getElementById('laneWidth') || document.getElementById('agttmWidth');
    const dEl = document.getElementById('agttmD');
    const tEl = document.getElementById('agttmT');
    const shiftEl = document.getElementById('agttmShift');
    const shoulderEl = document.getElementById('agttmShoulder');
    if (!speedEl || !laneWidthEl || !dEl || !tEl || !shiftEl || !shoulderEl) return;

    const speed = safeNumber(speedEl.value, 60);
    const width = safeNumber(laneWidthEl.value, 3.5);

    let d = 15;
    let baseMergeTaper = 15;

    if (speed <= 50) {
      d = 15;
      baseMergeTaper = 15;
    } else if (speed === 60) {
      d = 15;
      baseMergeTaper = 30;
    } else if (speed === 70) {
      d = 115;
      baseMergeTaper = 70;
    } else if (speed === 80) {
      d = 115;
      baseMergeTaper = 80;
    } else if (speed === 90) {
      d = 115;
      baseMergeTaper = 90;
    } else if (speed === 100) {
      d = 115;
      baseMergeTaper = 100;
    } else if (speed >= 110) {
      d = 115;
      baseMergeTaper = 110;
    }

    const adjustedTaper = Math.max(15, Math.round(baseMergeTaper * (width / 3.5)));
    const shiftTaper = Math.max(15, Math.round(adjustedTaper / 2));
    const shoulderTaper = Math.max(15, Math.round(adjustedTaper / 3));

    dEl.textContent = d + 'm';
    tEl.textContent = adjustedTaper + 'm';
    shiftEl.textContent = shiftTaper + 'm';
    shoulderEl.textContent = shoulderTaper + 'm';

    setFormula('agttmD', 'Dimension D from AGTTM speed table');
    setFormula('agttmT', 'Merge taper T = max(15, round(T_base * laneWidth/3.5)) = ' + adjustedTaper + 'm');
    setFormula('agttmShift', 'Shift taper = max(15, round(T/2)) = ' + shiftTaper + 'm');
    setFormula('agttmShoulder', 'Shoulder taper = max(15, round(T/3)) = ' + shoulderTaper + 'm');

    validateAGTTMGeometry();
    refreshFormulaAnnotations();
  }

  function ensureFormulaTraceAlwaysVisible() {
    const formulaTrace = document.getElementById('formulaTraceSection');
    if (!formulaTrace) return;

    if ('open' in formulaTrace) formulaTrace.open = true;
    const summary = formulaTrace.querySelector('summary');
    if (summary) summary.style.display = 'none';

    formulaTrace.addEventListener('toggle', function () {
      if ('open' in formulaTrace && !formulaTrace.open) formulaTrace.open = true;
    });
  }

  function bindSharedHandlers() {
    const roadClassEl = document.getElementById('roadClass');
    if (roadClassEl) {
      roadClassEl.addEventListener('change', updateGeometryDefaults);
    }

    const agttmSpeedEl = document.getElementById('agttmSpeed');
    if (agttmSpeedEl) {
      agttmSpeedEl.addEventListener('change', calculateAgttmGeometry);
    }

    const laneWidthEl = document.getElementById('laneWidth');
    if (laneWidthEl) {
      laneWidthEl.addEventListener('input', function () {
        validateAGTTMGeometry();
        calculateAgttmGeometry();
      });
      laneWidthEl.addEventListener('change', validateAGTTMGeometry);
    }

    const legacyWidthEl = document.getElementById('agttmWidth');
    if (!laneWidthEl && legacyWidthEl) {
      legacyWidthEl.addEventListener('input', calculateAgttmGeometry);
      legacyWidthEl.addEventListener('change', calculateAgttmGeometry);
    }

    // Expose shared functions so both pages can call the same implementations.
    window.validateAGTTMGeometry = validateAGTTMGeometry;
    window.updateGeometryDefaults = updateGeometryDefaults;
    window.calculateAgttmGeometry = calculateAgttmGeometry;

    calculateAgttmGeometry();

    if (/index_formulas\.html$/i.test(String(window.location.pathname || ''))) {
      ensureFormulaTraceAlwaysVisible();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindSharedHandlers);
  } else {
    bindSharedHandlers();
  }
})();
