# Full MIMIC Imbalance Retraining Methodology

This project trains a research-only MIMIC-IV-ED acuity model from triage-time
inputs. It is not clinically validated and every output requires clinician
review.

## Why This Patch Exists

The current results show strong high-acuity recall but excess over-triage. A
model can look safe on recall alone by predicting too many patients as urgent.
The retraining methodology therefore reports recall together with precision,
specificity, predicted urgent rate, over-triage, under-triage, confusion matrix,
ROC-AUC, and PR-AUC.

## Class Imbalance

MIMIC-IV-ED acuity labels are highly imbalanced: acuity 3 dominates and acuity 5
is rare. The training script now writes:

- `full_mimic_class_distribution.json`
- `full_mimic_class_distribution.csv`

These files report counts and percentages for acuity 1-5 overall and separately
for train, validation, and test. They also report acuity 1-2 as high acuity and
acuity 3-5 as non-high acuity.

## Split Policy

The default split is stratified patient-grouped train/validation/test.

- Patient grouping prevents repeat-patient leakage.
- Stratification preserves the class distribution.
- Validation and test remain natural, imbalanced, and untouched.
- No balancing or SMOTE is applied before splitting.

## Imbalance Strategies Compared

The training comparison now tags every candidate with:

- `imbalance_strategy`
- `class_weight_mode`
- `sampler`
- `train_distribution_before`
- `train_distribution_after`
- `validation_distribution`
- `test_distribution`

The intended comparison is:

- no balancing baseline
- `class_weight="balanced"` or balanced sample-weight baseline
- SMOTE after split, training only, inside imbalanced-learn pipelines

SMOTE is only applied to numeric feature spaces. For raw chief-complaint text
models, SMOTE is applied after TF-IDF and SVD, not directly to sparse raw text.

## New SMOTE Candidates

- `structured_logistic_smote`
- `structured_xgboost_smote`
- `raw_tfidf_svd_xgboost_smote`
- `raw_tfidf_svd_lightgbm_smote`
- `raw_tfidf_svd_catboost_smote`

These require the training environment dependency `imbalanced-learn`.

## Curve Artifacts

For the selected model, the binary high-acuity view is:

- positive class: acuity 1-2
- negative class: acuity 3-5

The training run writes:

- `selected_model_roc_curve.csv`
- `selected_model_pr_curve.csv`
- `selected_model_roc_curve.png`
- `selected_model_pr_curve.png`
- `selected_model_binary_curve_report.json`
- `all_models_roc_auc_comparison.csv`

PR-AUC is especially important when over-triage is a concern because it exposes
the precision/recall trade-off.

## Smoke Test

Run a quick smoke test before the H100 run:

```bash
python -m ml_training.full_mimic.compare_models --quick-test
```

Then run the SMOTE-specific smoke test. This is required because the default
`--quick-test` path only checks lightweight baseline models and does not prove
that imbalanced-learn or the TF-IDF/SVD/SMOTE pipelines are usable:

```bash
python -m ml_training.full_mimic.compare_models \
  --quick-test \
  --candidates structured_logistic_smote,raw_tfidf_svd_xgboost_smote \
  --min-high-acuity-recall 0 \
  --min-specificity 0 \
  --max-predicted-urgent-rate 1
```

The supplied `train_full_mimic.slurm` and `train_rescue_mimic.slurm` scripts run
this SMOTE smoke test into a separate `smote_smoke/` output directory before the
long comparison job.

## Final H100 Run

Use the SLURM script for the final evidence-generating run. It pins the explicit
candidate list, including the no-weight, class-weight, SMOTE, TF-IDF/SVD, and
GPU candidates. Do not use a broad all-candidate selector for the final one-time
evidence run because it can change as candidates are added.

```bash
sbatch train_full_mimic.slurm
```

If the full job fails because of GPU/library environment issues, use the smaller
rescue run:

```bash
sbatch train_rescue_mimic.slurm
```

For manual local reproduction only, use the same explicit candidate list as the
SLURM scripts:

```bash
FINAL_CANDIDATES="logistic_regression_unweighted,logistic_regression,raw_tfidf_word_char_logistic,raw_tfidf_word_char_sgd_logistic,raw_tfidf_word_char_linear_svm,raw_tfidf_svd_xgboost,raw_tfidf_svd_lightgbm,raw_tfidf_svd_catboost,structured_logistic_smote,structured_xgboost_smote,raw_tfidf_svd_xgboost_smote,raw_tfidf_svd_lightgbm_smote,raw_tfidf_svd_catboost_smote,xgboost_gpu,catboost_gpu,lightgbm_gpu"
python -m ml_training.full_mimic.compare_models \
  --candidates "$FINAL_CANDIDATES" \
  --selection-profile balanced_safety
```

For a stricter over-triage sensitivity run:

```bash
FINAL_CANDIDATES="logistic_regression_unweighted,logistic_regression,raw_tfidf_word_char_logistic,raw_tfidf_word_char_sgd_logistic,raw_tfidf_word_char_linear_svm,raw_tfidf_svd_xgboost,raw_tfidf_svd_lightgbm,raw_tfidf_svd_catboost,structured_logistic_smote,structured_xgboost_smote,raw_tfidf_svd_xgboost_smote,raw_tfidf_svd_lightgbm_smote,raw_tfidf_svd_catboost_smote,xgboost_gpu,catboost_gpu,lightgbm_gpu"
python -m ml_training.full_mimic.compare_models \
  --candidates "$FINAL_CANDIDATES" \
  --selection-profile balanced_safety \
  --min-specificity 0.30 \
  --max-predicted-urgent-rate 0.75 \
  --min-high-acuity-recall 0.80
```

Do not select a model on recall alone.
