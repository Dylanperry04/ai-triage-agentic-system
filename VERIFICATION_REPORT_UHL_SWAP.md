# Verification report — 22.4 UHL data/model swap

Date: 2026-08-11

## Source identity

- Gospel baseline: `ai-triage-agentic-system-v22.4.0.zip`
  - SHA-256: `71e12c6f3d51b8235fd8bd9f2dd0e52d0d405b216222f0bbe929a072f3f81a8d`
- UHL payload source: `ai-triage-uhl-23.1.1-azure-final.zip`
  - SHA-256: `1d7a76f38826dc9f2bba459ce48e69a2cc63e1faeaada1facb9c36cb4ad634d3`

Both input archives were treated as read-only and were not modified.

## Migration boundary

The working 22.4 application is the base. Only the active dataset repository,
feature/serving contract, selected model, evidence/report adapter, UHL-dependent
health/status metadata, UHL-dependent UI labels, and deployment asset paths were
changed.

The rewritten 23.1.1 application architecture, schemas, state implementation,
security replacement, API redesign, frontend redesign, and replacement test
suite were not imported.

## Pinned UHL inputs

- Dataset: `data/uhl_dataset_final.csv.gz`
  - 777,176 rows
  - SHA-256: `f3a6b4b8c7ee081fc02c924978ee1c5ecb5d7ebffbd32a2058d10cbd1bf1cd5c`
- Selected model: `artifacts/model/uhl_synthetic_acuity_selected.joblib`
  - CatBoost 1.2.5 bundle
  - Run ID: `68827117-f043-437f-96c8-7f02e322e40c`
  - SHA-256: `7dddf3cc673f5598d73d7e6d56546cad49639edcae77b44b17b677f0b0d1395b`
- Feature-schema SHA-256:
  `fd3d1365fe744d5eb75a83b8cfb1ebf9b84695a405c802b633bd2bb78f89debd`

The model-serving frame is restricted to the exact 13-field contract embedded
in the artifact. The two `DOA` rows are outside model scope and route away from
ML prediction.

## Follow-up interface and recommendation changes

- The recommendation starts with the modal class and moves upward only when an
  individual more-urgent class reaches 25%; the most urgent qualifying class
  wins.
- For the screenshot case (2.4%, 12.1%, 31.0%, 42.0%, 12.5%), the authoritative
  result is now Acuity 3, with 31.0% displayed and logged.
- The previous decision-rule explanation box is hidden.
- The right-column action stack uses compact button padding and icon sizing.
- Prototype/synthetic-data/disclaimer wording was removed from the visible
  React interface and generated production bundle.

## Verification results

- Azure/local preflight: PASS
  - dataset/model present and hashes match
  - model bundle is deployment-eligible and satisfies the serving contract
  - reports and built React UI are present
  - Docker packaging includes UHL assets
- Backend regression suite: **1,007 passed, 21 skipped**
  - the skipped tests are archived assertions that explicitly require MIMIC to
    remain the active runtime source
  - UHL replacement coverage: `tests/test_uhl_22_4_swap.py`
- Focused deployment/UHL/governance/raw-ID tests: **51 passed**
- React tests: **40 passed**
- React production build: PASS
- API smoke:
  - `/health`: 200
  - `/status/uhl`: 200
  - `/runtime/status`: 200
  - `/system/meta`: 200
  - `/cases?limit=2`: 200, total 777,176, UHL source
  - preview assessment: 200, UHL ML prediction available
  - `/model/performance`: 200, UHL evidence available

## Supplied model evidence

- High-acuity recall: 0.960193
- Macro F1: 0.124583
- Severe under-triage rate: 0.000120
- Under-triage rate: 0.011349
- Within-one-level accuracy: 0.852911

These figures describe the supplied artifact's original evaluated ordinal-cost
rule. They do not quantify the new modal-plus-25% deployment post-processing;
that rule requires a fresh locked-test evaluation before performance claims are
updated.

## Packaging and security notes

The deliverable excludes the old MIMIC model/report bundle, MIMIC-shaped demo
cohort, generated SQLite cache, runtime audit/workflow logs, bytecode, test
caches, and `node_modules`. The checked-in React `dist` build is retained.

The baseline archive contained an Azure OpenAI key-looking value in
`.env.example`. The corrected package replaces it with a placeholder. Treat the
original value as exposed and rotate/revoke it in Azure; the original input ZIP
was intentionally left untouched.

## Limitations

This remains a synthetic-data research prototype and is not clinically
validated. The selected model has high high-acuity recall but weak overall
discrimination and a high urgent-prediction rate. A NumPy/CatBoost ABI-size
warning appears in the local Python 3.10 test environment; artifact loading and
inference pass, and the deployment requirements pin the training-compatible
stack. See `docs/MODEL_CARD_LIMITATIONS.md` for the full no-go clinical verdict.
