"""
DEPRECATED / DISABLED — do not use.

This script previously downloaded the full credentialed MIMIC-IV-ED dataset into
`data/raw/` INSIDE the repository. That violates the PhysioNet Credentialed Data
Use Agreement: credentialed data must never be copied into the repo, a build
artifact, a Docker image, or any shared location.

The full dataset is now handled by the DUA-safe seam:
  - Set MIMIC_FULL_ED_DIR to a path OUTSIDE this repo on your credentialed,
    approved environment (PATIENT_DATA_MODE=true).
  - Use the scaffolding in ml_training/full_mimic/ (schema verification, feature
    building, training, evaluation, model/dataset cards). Those scripts refuse
    repo-local paths and emit aggregate/de-identified outputs only.

See docs/SECURITY_ARCHITECTURE.md and app/data_pipeline/mimic_full_loader.py.
"""
import sys


def main() -> int:
    sys.stderr.write(
        "load_full_mimic_ed.py is DISABLED. It used to download credentialed data "
        "into data/raw/ inside the repo, which violates the PhysioNet DUA.\n\n"
        "Use the DUA-safe path instead:\n"
        "  export MIMIC_FULL_ED_DIR=/secure/path/outside/repo/ed   # credentialed env\n"
        "  export PATIENT_DATA_MODE=true\n"
        "  python ml_training/full_mimic/verify_schema.py\n"
        "  python ml_training/full_mimic/build_features.py\n"
        "  python ml_training/full_mimic/train.py\n"
        "  python ml_training/full_mimic/evaluate.py\n\n"
        "See docs/SECURITY_ARCHITECTURE.md.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
