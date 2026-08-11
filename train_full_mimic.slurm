#!/bin/bash
#SBATCH -p student
#SBATCH --job-name=mimic_train
#SBATCH --output=train_%j.out
#SBATCH --error=train_%j.err
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=16
#SBATCH --mem=180G
#SBATCH --gres=gpu:2

set -euo pipefail

cd /home/dylan.perry
source .venv-training/bin/activate

export PYTHONUNBUFFERED=1
export LOCAL_CREDENTIALED_RESEARCH=true

export DATA_ROOT="/tmp/dylan.perry-mimic"
export MIMIC_FULL_ED_DIR="$DATA_ROOT/mimic-iv-ed-2.2-clean/ed"
export MIMIC_FULL_OUTPUT_DIR="$DATA_ROOT/mimic_training_outputs/run_${SLURM_JOB_ID}"
export MIMIC_FULL_MODEL_REPORT_DIR="$MIMIC_FULL_OUTPUT_DIR"

mkdir -p "$MIMIC_FULL_OUTPUT_DIR"
mkdir -p "$DATA_ROOT/tmp_${SLURM_JOB_ID}"
mkdir -p "$DATA_ROOT/joblib_${SLURM_JOB_ID}"

export TMPDIR="$DATA_ROOT/tmp_${SLURM_JOB_ID}"
export JOBLIB_TEMP_FOLDER="$DATA_ROOT/joblib_${SLURM_JOB_ID}"

export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

export MIMIC_COMPARE_N_ESTIMATORS=1200
export MIMIC_COMPARE_GPU_N_ESTIMATORS=2000
export MIMIC_COMPARE_CATBOOST_ITERATIONS=1200
export MIMIC_COMPARE_GPU_ITERATIONS=2000

export MIMIC_TFIDF_LOGISTIC_SOLVER="saga"
export MIMIC_TFIDF_WORD_MAX_FEATURES=50000
export MIMIC_TFIDF_CHAR_MAX_FEATURES=50000
export MIMIC_TFIDF_SVD_COMPONENTS=512

export MIMIC_SELECTION_PROFILE="balanced_safety"
export MIMIC_MIN_SPECIFICITY=0.20
export MIMIC_MAX_PREDICTED_URGENT_RATE=0.85
# Main final run floor. Use stricter values such as 0.97 only as a separate
# sensitivity run; otherwise the final job may safely end with NO_MODEL_SELECTED.
export MIMIC_MIN_HIGH_ACUITY_RECALL=0.80

export MIMIC_CATBOOST_GPU_DEVICES="0:1"
export MIMIC_XGB_DEVICE="cuda"

SMOTE_SMOKE_CANDIDATES="structured_logistic_smote,raw_tfidf_svd_xgboost_smote"
FINAL_CANDIDATES="logistic_regression_unweighted,logistic_regression,raw_tfidf_word_char_logistic,raw_tfidf_word_char_sgd_logistic,raw_tfidf_word_char_linear_svm,raw_tfidf_svd_xgboost,raw_tfidf_svd_lightgbm,raw_tfidf_svd_catboost,structured_logistic_smote,structured_xgboost_smote,raw_tfidf_svd_xgboost_smote,raw_tfidf_svd_lightgbm_smote,raw_tfidf_svd_catboost_smote,xgboost_gpu,catboost_gpu,lightgbm_gpu"

echo "Job ID: $SLURM_JOB_ID"
echo "Node:"
hostname
echo "GPU check:"
nvidia-smi || true

echo "Schema check first..."
python -u -m ml_training.full_mimic.verify_schema

echo "SMOTE smoke test first..."
SMOTE_SMOKE_OUTPUT_DIR="$MIMIC_FULL_OUTPUT_DIR/smote_smoke"
mkdir -p "$SMOTE_SMOKE_OUTPUT_DIR"
MIMIC_FULL_OUTPUT_DIR="$SMOTE_SMOKE_OUTPUT_DIR" \
MIMIC_FULL_MODEL_REPORT_DIR="$SMOTE_SMOKE_OUTPUT_DIR" \
python -u -m ml_training.full_mimic.compare_models \
  --quick-test \
  --selection-profile balanced_safety \
  --min-high-acuity-recall 0 \
  --min-specificity 0 \
  --max-predicted-urgent-rate 1 \
  --candidates "$SMOTE_SMOKE_CANDIDATES"

echo "Starting final model comparison..."
python -u -m ml_training.full_mimic.compare_models \
  --selection-profile balanced_safety \
  --candidates "$FINAL_CANDIDATES"

echo "Checking selected artifact compatibility..."
python -u -m ml_training.full_mimic.check_artifact_compatibility \
  "$MIMIC_FULL_OUTPUT_DIR/mimic_full_acuity_selected.joblib"

echo "DONE"
echo "Outputs written to: $MIMIC_FULL_OUTPUT_DIR"
ls -lh "$MIMIC_FULL_OUTPUT_DIR"
