# Verification report

> **v18 update:** the deployed system is now a SINGLE service — the FastAPI
> backend serves the built React UI. References below to Streamlit tabs
> (explainability, cost, maintainability, etc.) describe the RETIRED Streamlit
> frontend and are retained only as a record of the source inspected at the
> time. The React UI folds explainability into the advisory panel and drops the
> cost/runtime view by design. The bundled model artefact and reports remain the
> last credentialed training run (run_15); the SMOTE/class-weight retraining is a
> separate, still-pending run.

Bundle: latest available code plus existing last-trained model artefact and reports.

## Source inputs used

- Code source used: `v17_fixed(16).zip` from the current sandbox, because it is the latest implementation package inspected with the audit dashboard, role-based navigation, recall-by-triage-category, explainability, cost, maintainability, and UI-template updates.
- Training artefact source used: `full_repo_backup_20260708_125520(2).zip/private_mimic_outputs/run_15`.
- The newly uploaded `v17_fixed (23) (1)(3).zip` was checked and matched the earlier `v17_fixed (22).zip` SHA. It did **not** contain a trained model artefact or training output reports.

## Included model

- File: `model_outputs/last_training/mimic_full_acuity_selected.joblib`
- SHA256: `3278f566ee2b041f633accd8dd88834e6e33b0b5a1f98864335dd13d096681d8`
- Training run ID: `1e058630-5a5b-481b-9fc9-a91866c85d8d`
- Selected model: `raw_tfidf_word_char_sgd_logistic`
- Dataset: `MIMIC-IV-ED-Full-v2.2`
- Split: stratified patient-grouped
- Train/validation/test: 291,853 / 63,030 / 63,217 labelled stays

## Verification performed in sandbox

- Compile check passed:
  - `python -m compileall -q app ml_training frontend scripts tests`
- Targeted executable tests passed:
  - `111 passed, 6 warnings`
- Model performance endpoint check passed with environment variables pointing to bundled model/report directory:
  - `GET /model/performance` returned 200
  - status: `available`
  - model_file_exists: `true`
  - model_hash_configured: `true`
  - model_provenance_status: `verified`
  - roc_curve points: 19
  - pr_curve points: 19

## Important limitation

The sandbox uses Python 3.13 and scikit-learn 1.8, while the model artefact was trained with scikit-learn 1.5.1. The code requirements pin `scikit-learn==1.5.1`, so Azure/Python 3.11 must install the pinned requirements. Full joblib deserialization/prediction was not verified in this sandbox because deserializing under sklearn 1.8 fails for this old artefact.

## Deployment app settings

Use `deployment/AZURE_APP_SETTINGS_LAST_TRAINING.txt`.

## Clinical limitation

This remains a research/demo deployment artefact only. It is not clinically validated and must not be used for real patient-care triage decisions.
