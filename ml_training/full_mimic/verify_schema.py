"""Full-MIMIC schema verification. Confirms the credentialed tables match the
expected schema BEFORE any training. Aggregate output only (column lists, row
counts, required-missing) — never raw rows.

Run on the credentialed environment:
  MIMIC_FULL_ED_DIR=/secure/.../ed MIMIC_FULL_OUTPUT_DIR=/secure/out \
  PATIENT_DATA_MODE=true python ml_training/full_mimic/verify_schema.py
"""
import json
import sys

from ml_training.full_mimic._safety import require_safe_environment, UnsafeEnvironmentError


def main() -> int:
    try:
        paths = require_safe_environment()
    except UnsafeEnvironmentError as e:
        sys.stderr.write(f"REFUSED: {e}\n")
        return 2
    # Reuse the validated, guarded adapter validation.
    from app.config import settings
    settings.mimic_full_ed_dir = paths["ed_dir"]  # in-memory only
    from app.data_pipeline.mimic_full_loader import validate_full_mimic_schema
    report = validate_full_mimic_schema()
    out = paths["output_dir"] / "full_mimic_schema_report.json"
    out.write_text(json.dumps(report, indent=2))
    print(f"Schema report written: {out}")
    print(f"  all_required_present: {report.get('all_required_present')}")
    for t, info in report.get("tables", {}).items():
        print(f"  {t}: {info.get('status')} ({info.get('row_count','?')} rows)")
    return 0 if report.get("all_required_present") else 1


if __name__ == "__main__":
    raise SystemExit(main())
