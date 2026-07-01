"""Full-MIMIC dataset card generator. Reads the aggregate schema report and writes
a Markdown dataset card. Reads only aggregate JSON; no raw rows.

Run on the credentialed environment AFTER verify_schema.py.
"""
import json
import sys
from datetime import date


def main() -> int:
    from ml_training.full_mimic._safety import require_safe_environment, UnsafeEnvironmentError
    try:
        paths = require_safe_environment()
    except UnsafeEnvironmentError as e:
        sys.stderr.write(f"REFUSED: {e}\n")
        return 2
    out = paths["output_dir"]
    schema_p = out / "full_mimic_schema_report.json"
    schema = json.loads(schema_p.read_text()) if schema_p.exists() else {}
    tables = schema.get("tables", {})
    rows = "\n".join(
        f"- **{t}**: {i.get('status')}, {i.get('row_count','?')} rows"
        for t, i in tables.items()
    )
    md = f"""# Dataset Card — MIMIC-IV-ED (Full, Credentialed)

**Generated:** {date.today().isoformat()}
**Access:** PhysioNet Credentialed Health Data License 1.5.0 (DUA required).
**Handling:** Read only from a controlled MIMIC_FULL_ED_DIR on an approved
environment. NEVER copied into the repo, Docker image, build artefact, tests, or
any shared location.

## Source
MIMIC-IV-ED v2.2 (Beth Israel Deaconess). Six tables: edstays, triage, vitalsign,
diagnosis, medrecon, pyxis.

## Tables (aggregate, from schema verification)
{rows or '- (run verify_schema.py first)'}

## Fields used / excluded
The feature builder uses triage + vitals features and EXCLUDES outcome/label
leakage columns. Raw identifiers (subject_id, stay_id, hadm_id) are used only for
internal joins and are never emitted in artefacts (replaced by a pseudonymous
case_uid in any downstream record).

## Known bias / fairness risks
ED acuity labels are human triage assessments and may encode bias. Review feature
importances for reliance on sensitive attributes (e.g. race) before any use.

## Governance
Research only; UHL validation pending governance/data-protection/clinical approval.
"""
    (out / "full_mimic_dataset_card.md").write_text(md)
    print(f"Dataset card written: {out/'full_mimic_dataset_card.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
