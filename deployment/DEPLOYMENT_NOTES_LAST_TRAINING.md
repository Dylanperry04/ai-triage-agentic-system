# Deployment notes: latest code + existing last-trained model

This deployment bundle contains the latest available source code plus the existing last-trained full-MIMIC model artefact and aggregate model reports under:

`model_outputs/last_training/`

The selected model artefact is:

`model_outputs/last_training/mimic_full_acuity_selected.joblib`

SHA256:

`3278f566ee2b041f633accd8dd88834e6e33b0b5a1f98864335dd13d096681d8`

Set the Azure App Service environment variables shown in `deployment/AZURE_APP_SETTINGS_LAST_TRAINING.txt`.

Important limitations:

- This uses the previous training run, not the new SMOTE/class-weight retraining methodology.
- The previous training run selected `raw_tfidf_word_char_sgd_logistic`.
- The model was trained with scikit-learn 1.5.1. Keep the pinned runtime requirements.
- The model and reports are research artefacts only and are not clinically validated.
- v18: the deployment bundle DOES contain the trained model artefact — `model_outputs/last_training/mimic_full_acuity_selected.joblib` (training_run_id 1e058630-5a5b-481b-9fc9-a91866c85d8d, SHA-256 pinned in `mimic_full_model_sha256.txt` and verified by scripts/azure_preflight_check.py). These artefacts originate from the last credentialed training run (`full_repo_backup_20260708_125520(2).zip/private_mimic_outputs/run_15`).
