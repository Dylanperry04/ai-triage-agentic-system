# ML Model Overview

This document summarises the model families used in the AI triage research
system. The models estimate MIMIC-IV-ED-style acuity from triage-time inputs.
They are not an official Manchester Triage System implementation and are not
clinically validated.

## Modelling Aim

The research goal is to estimate acuity from information available at or around
triage:

- chief complaint text
- triage vital signs
- pain field and missingness/outlier flags
- arrival transport
- limited registration-time demographics where configured

The model must not use post-triage or outcome-linked fields such as
disposition, diagnosis, medrecon, pyxis, outtime, mortality/death fields, or
future vitals.

## Model Families

| Family | Candidates | Input type | Main purpose |
|---|---|---|---|
| Regression / linear | `logistic_regression`, `logistic_regression_unweighted`, `raw_tfidf_word_char_logistic`, `raw_tfidf_word_char_sgd_logistic` | structured features or raw triage dataframe with TF-IDF | strong transparent baselines; useful for class-weight comparisons |
| Linear margin | `raw_tfidf_word_char_linear_svm` | raw triage dataframe with TF-IDF | high-dimensional text baseline; may not provide calibrated probabilities |
| Tree-based | `random_forest`, `extra_trees`, structured tree candidates where present | structured feature matrix | non-linear tabular baseline |
| Boosted ensembles | `xgboost_gpu`, `lightgbm_gpu`, `catboost_gpu`, SVD boosted candidates | structured or TF-IDF/SVD numeric features | stronger non-linear modelling and GPU-enabled comparison |
| Text/vector | raw TF-IDF, TF-IDF + SVD candidates | chief complaint text plus structured features | captures ED abbreviation and complaint-text signal |
| Imbalance strategies | no weighting, `class_weight="balanced"`, balanced sample weights, SMOTE after split | training-only | tests whether imbalance handling improves recall without excessive over-triage |

## Candidate Map

| Candidate | Family | Input | Imbalance strategy | Deployment eligibility |
|---|---|---|---|---|
| `logistic_regression_unweighted` | Regression / linear | structured features | none | yes, if selected and artifact compatibility passes |
| `logistic_regression` | Regression / linear | structured features | `class_weight="balanced"` | yes, if selected and artifact compatibility passes |
| `raw_tfidf_word_char_logistic` | Regression / linear + text/vector | raw triage dataframe | balanced class weights | yes, v2 raw-column artifact |
| `raw_tfidf_word_char_sgd_logistic` | Regression / linear + text/vector | raw triage dataframe | balanced class weights | yes, v2 raw-column artifact |
| `raw_tfidf_word_char_linear_svm` | Linear margin + text/vector | raw triage dataframe | balanced class weights | comparison/selection depends on probability support |
| `raw_tfidf_svd_xgboost` | Boosted ensemble + text/vector | TF-IDF/SVD numeric features | sample-weight or model-specific balancing where supported | yes, v2 raw-column artifact |
| `raw_tfidf_svd_lightgbm` | Boosted ensemble + text/vector | TF-IDF/SVD numeric features | model-specific balancing where supported | yes, v2 raw-column artifact |
| `raw_tfidf_svd_catboost` | Boosted ensemble + text/vector | TF-IDF/SVD numeric features | model-specific balancing where supported | yes, v2 raw-column artifact |
| `structured_logistic_smote` | Regression / linear | structured numeric features | SMOTE after split, training-only | yes, app runtime includes imbalanced-learn |
| `structured_xgboost_smote` | Boosted ensemble | structured numeric features | SMOTE after split, training-only | yes, app runtime includes imbalanced-learn |
| `raw_tfidf_svd_xgboost_smote` | Boosted ensemble + text/vector | TF-IDF/SVD numeric features | SMOTE after split, training-only | yes, app runtime includes imbalanced-learn |
| `raw_tfidf_svd_lightgbm_smote` | Boosted ensemble + text/vector | TF-IDF/SVD numeric features | SMOTE after split, training-only | yes, app runtime includes imbalanced-learn |
| `raw_tfidf_svd_catboost_smote` | Boosted ensemble + text/vector | TF-IDF/SVD numeric features | SMOTE after split, training-only | yes, app runtime includes imbalanced-learn |
| `xgboost_gpu` | Boosted ensemble | structured or engineered features | candidate-specific weighting/sample-weight handling | yes, if selected and artifact compatibility passes |
| `lightgbm_gpu` | Boosted ensemble | structured or engineered features | candidate-specific balancing | yes, if selected and artifact compatibility passes |
| `catboost_gpu` | Boosted ensemble | structured or engineered features | candidate-specific balancing | yes, if selected and artifact compatibility passes |

## Imbalance Methodology

The correct sequence is:

1. Load labelled MIMIC-IV-ED cases.
2. Split by stratified patient groups into train/validation/test.
3. Keep validation and test in the natural imbalanced distribution.
4. Apply class weights or SMOTE only inside the training pipeline.
5. Select using balanced safety metrics, not recall alone.

This avoids the common leakage error of balancing the full dataset before
splitting.

## Evaluation Focus

The dashboard reports:

- per-class recall
- overall recall
- high-acuity recall for acuity 1-2 vs 3-5
- specificity
- high-acuity precision
- predicted urgent rate
- over-triage and under-triage rates
- severe under-triage rate
- accuracy, macro F1, weighted F1
- AUROC and PR-AUC when probabilities are available
- confusion matrix, ROC curve, and precision-recall curve

High recall is not sufficient by itself. A model can achieve very high recall by
marking too many cases urgent, so specificity and predicted urgent rate are part
of the selection constraints.

## Explainability Limits

Feature importance is reported only where methodologically supported:

- linear models: coefficients
- tree/boosting models: feature importances or native importance where exposed
- unsupported models: reported as unavailable
- TF-IDF/SVD models: marked as not directly interpretable when SVD prevents valid
  token-level mapping

No feature-importance report may include leakage fields.
