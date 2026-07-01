"""Full-MIMIC feature builder. Loads credentialed cases via the guarded loader,
builds the model feature frame, and writes ONLY an aggregate feature summary +
the feature list (no raw rows, no identifiers).

Run on the credentialed environment (see verify_schema.py for env vars).
"""
import json
import sys

from ml_training.full_mimic._safety import (
    require_safe_environment, assert_no_raw_rows, UnsafeEnvironmentError,
)


def main() -> int:
    try:
        paths = require_safe_environment()
    except UnsafeEnvironmentError as e:
        sys.stderr.write(f"REFUSED: {e}\n")
        return 2
    from app.config import settings
    settings.mimic_full_ed_dir = paths["ed_dir"]
    from app.data_pipeline.mimic_full_loader import load_mimic_full_cases

    cases = load_mimic_full_cases()
    # Build features using the SAME engineering as the demo pipeline so the full
    # model is compatible. We import lazily to avoid heavy deps at import time.
    from ml_training.feature_engineering import build_feature_frame  # type: ignore
    try:
        X, y, feature_names = build_feature_frame(cases)
    except Exception as exc:
        sys.stderr.write(f"Feature build failed: {exc}\n")
        return 1

    # Aggregate-only summary.
    import numpy as np
    summary = {
        "n_cases": int(len(cases)),
        "n_features": int(len(feature_names)),
        "n_labelled": int(sum(1 for v in y if v is not None)),
        "class_balance": {str(k): int(v) for k, v in
                          zip(*np.unique([v for v in y if v is not None], return_counts=True))}
        if any(v is not None for v in y) else {},
    }
    assert_no_raw_rows(summary)
    (paths["output_dir"] / "full_mimic_feature_summary.json").write_text(json.dumps(summary, indent=2))
    (paths["output_dir"] / "full_mimic_feature_list.json").write_text(json.dumps(list(feature_names), indent=2))
    print(f"Feature summary + feature list written to {paths['output_dir']}")
    print(f"  cases={summary['n_cases']} features={summary['n_features']} labelled={summary['n_labelled']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
