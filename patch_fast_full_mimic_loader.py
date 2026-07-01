from pathlib import Path
import re

path = Path("app/data_pipeline/mimic_full_loader.py")
text = path.read_text(encoding="utf-8")

new_func = '''def load_mimic_full_cases(n=None):
    """
    Fast full-MIMIC case loader for training/feature building.

    Uses all eligible full-MIMIC ED stays from edstays.csv.gz + triage.csv.gz.
    Does not expand vitalsign/diagnosis/medrecon/pyxis because those are large
    auxiliary/post-triage tables and are not used by the triage-time feature
    builder. validate_full_mimic_schema() still checks all six tables.
    """
    import pandas as pd
    path = _assert_safe_to_use()
    from app.data_pipeline.mimic_adapter import (
        load_mimic_table,
        dataframe_to_cases,
        EXPECTED_COLUMNS,
        SOURCE_DATASET_LABEL_FULL,
    )

    edstays = load_mimic_table(path, "edstays")
    triage = load_mimic_table(path, "triage")

    empty_vitalsign = pd.DataFrame(columns=EXPECTED_COLUMNS["vitalsign"])
    empty_diagnosis = pd.DataFrame(columns=EXPECTED_COLUMNS["diagnosis"])
    empty_medrecon = pd.DataFrame(columns=EXPECTED_COLUMNS["medrecon"])
    empty_pyxis = pd.DataFrame(columns=EXPECTED_COLUMNS["pyxis"])

    return dataframe_to_cases(
        edstays,
        triage,
        empty_vitalsign,
        empty_diagnosis,
        empty_medrecon,
        empty_pyxis,
        n=n,
        source_dataset_label=SOURCE_DATASET_LABEL_FULL,
    )

'''

pattern = r'(?ms)^def load_mimic_full_cases\(n=None\):.*?(?=^def |\Z)'
new_text, count = re.subn(pattern, new_func, text, count=1)

if count != 1:
    raise SystemExit(f"Patch failed: expected 1 replacement, got {count}")

path.write_text(new_text, encoding="utf-8")
print("Patched load_mimic_full_cases successfully.")
