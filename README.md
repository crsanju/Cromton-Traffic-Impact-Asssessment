# Cromton Traffic Impact Asssessment

This repository contains the Traffic Impact Assessment tool.

## Ownership

Copyright © 2026 Crompton Concepts. All rights reserved.

Unless stated otherwise in third-party dependencies or bundled assets, the source code and application materials in this repository are owned by Crompton Concepts.

## License

This repository is distributed under the proprietary terms set out in [LICENSE](LICENSE).

No public right to copy, modify, redistribute, or create derivative works is granted except where Crompton Concepts provides written authorization.

Third-party materials, if any, remain subject to their own applicable license terms.

## Index Sync Workflow

`index_formulas.html` is generated from `index.html` and must stay in sync.

1. Edit `index.html` first.
2. Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/sync-index-formulas.ps1
```

3. Stage your changes (the hook can auto-stage `index_formulas.html` when `index.html` is staged):

```powershell
git add index.html
```

The sync script keeps formula-mode behavior in `index_formulas.html` (formula enforcer block) while mirroring updates from `index.html`.

## Pre-commit Enforcement

Install the Git hook once per clone:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
```

After that, each commit auto-runs the sync whenever `index.html` is staged, then auto-stages `index_formulas.html`.
