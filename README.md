# Cromton Traffic Impact Assessment

This repository contains the Traffic Impact Assessment (TIA) tool for analyzing traffic impacts and generating professional reports.

## Ownership

Copyright © 2026 Crompton Concepts. All rights reserved.

Unless stated otherwise in third-party dependencies or bundled assets, the source code and application materials in this repository are owned by Crompton Concepts.

## License

This repository is distributed under the proprietary terms set out in [LICENSE](LICENSE).

No public right to copy, modify, redistribute, or create derivative works is granted except where Crompton Concepts provides written authorization.

Third-party materials, if any, remain subject to their own applicable license terms.

## Quick Start

### Installation
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Git hooks for workflow automation
powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
```

### Running the Application
```bash
# Start the Python report service
python report_service.py

# Open the frontend in browser
# File: index.html
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for comprehensive setup and deployment instructions.

## Documentation

- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Full setup, configuration, and deployment guide
- **[LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)** - Pre-launch verification checklist
- **[manual.html](manual.html)** - User manual (open in browser)
- **[LICENSE](LICENSE)** - Proprietary license terms
- **[COPYRIGHT.md](COPYRIGHT.md)** - Copyright notice

## Index Sync Workflow

Use this separation model:

1. `index.html` = main user interface (beta hidden).
2. `index_formulas.html` = user-facing formulas view (beta hidden), synced from `index.html`.
3. `index_developer.html` = isolated beta/developer editing file.

For user releases, sync only formulas from main index:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1
```

Optional reset command (only when you explicitly want to rebuild developer from stable main):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync-index-developer.ps1
```

Stage your changes (the hook auto-stages `index_formulas.html` when `index.html` is staged):

```powershell
git add index.html
```

The formulas sync keeps formula-mode behavior in `index_formulas.html` (formula enforcer block) while mirroring updates from `index.html`.

## Pre-commit Enforcement

Install the Git hook once per clone:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
```

After that, each commit auto-runs the sync whenever `index.html` is staged, then auto-stages `index_formulas.html`.
