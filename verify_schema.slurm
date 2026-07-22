#!/bin/bash
#SBATCH -p student
#SBATCH --job-name=mimic_schema
#SBATCH --output=schema_%j.out
#SBATCH --error=schema_%j.err
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G

set -euo pipefail

cd /home/dylan.perry
source .venv-training/bin/activate

export PYTHONUNBUFFERED=1
export LOCAL_CREDENTIALED_RESEARCH=true
export DATA_ROOT="/tmp/dylan.perry-mimic"
export MIMIC_FULL_ED_DIR="$DATA_ROOT/mimic-iv-ed-2.2-clean/ed"
export MIMIC_FULL_OUTPUT_DIR="$DATA_ROOT/mimic_training_outputs/schema_${SLURM_JOB_ID}"

mkdir -p "$MIMIC_FULL_OUTPUT_DIR"

python -u -m ml_training.full_mimic.verify_schema
