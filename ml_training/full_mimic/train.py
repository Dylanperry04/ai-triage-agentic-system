"""Deprecated full-MIMIC training entry point.

The earlier baseline trainer used a single RandomForest path with cross-
validation metrics. That is not the accepted research workflow because it does
not perform validation-only safety-first model selection and untouched-test
reporting. Use ``compare_models.py`` instead.
"""
from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "REFUSED: ml_training/full_mimic/train.py is deprecated. "
        "Use ml_training/full_mimic/compare_models.py so model selection is "
        "patient-grouped/temporal, validation-only, safety-first, and reports "
        "untouched-test metrics before any artefact is served.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
