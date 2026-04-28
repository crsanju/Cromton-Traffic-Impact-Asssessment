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
# Service URL: http://127.0.0.1:8060

# Optional: increase max request size for large report payloads (12 MB example)
# Command Prompt (cmd.exe)
set REPORT_MAX_REQUEST_BYTES=12000000
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

## Automated Dataset Update Pipeline

The repository now includes an automated release-detection and update flow for GeoJSON datasets.

- Scheduled workflow: `.github/workflows/dataset-update.yml` (runs every 6 hours, plus manual trigger)
- Update script: `scripts/check_and_update_datasets.py`
- Dataset manifest: `dataset_manifest.json`

### What It Does

1. Fetches each configured upstream dataset URL.
2. Validates payload shape (GeoJSON `FeatureCollection` or non-empty JSON list).
3. Compares SHA-256 against the last accepted manifest version.
4. Rejects suspicious row drops (default threshold: >30% drop).
5. Updates changed local dataset files and regenerates `dataset_manifest.json`.
6. Opens a pull request with the updated files.

### Manual Run

```bash
python scripts/check_and_update_datasets.py
```

### Reuse A Single Virtual Environment

Use one local environment (`venv`) and reuse it for all tasks:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/use-venv.ps1
```

Optional flags:

```powershell
# Reuse/create venv and install requirements
powershell -ExecutionPolicy Bypass -File scripts/use-venv.ps1 -InstallRequirements

# Reuse/create venv and run dataset updater
powershell -ExecutionPolicy Bypass -File scripts/use-venv.ps1 -RunDatasetUpdate
```

Cleanup helper (keeps `venv`, removes duplicate env folders like `.venv`, `venv-1`, `.env`, `env`):

```powershell
# Dry run (preview only)
powershell -ExecutionPolicy Bypass -File scripts/cleanup-extra-venvs.ps1

# Apply cleanup
powershell -ExecutionPolicy Bypass -File scripts/cleanup-extra-venvs.ps1 -Apply
```

### Frontend Cache Behavior

`index.html` reads `dataset_manifest.json` on load. If dataset hashes/versions changed since the last visit, cached dataset entries are cleared and fresh data is fetched automatically.
