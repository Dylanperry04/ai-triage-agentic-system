"""
MIMIC-IV-ED Demo v2.2 dataset adapter.

Reads the official PhysioNet MIMIC-IV-ED Demo v2.2 .csv.gz files and converts
them into the project's canonical EDTriageCase JSONL format.

THIS IS THE DEMO DATASET ONLY (222 ED stays, public, no PhysioNet
credentials required). It is NOT the full MIMIC-IV-ED dataset (~216,000
stays, requires a credentialed PhysioNet account, still pending approval
as of this writing). Do not present results from this adapter as if they
came from the full dataset.

VERIFIED FILE CHARACTERISTICS (checked directly against the actual
uploaded mimic-iv-ed-demo-2.2.zip, not assumed from documentation):
  - SHA256SUMS.txt is present and every file's checksum was independently
    recomputed and matched before this adapter was written.
  - Six real tables under ed/: diagnosis, edstays, medrecon, pyxis,
    triage, vitalsign -- all six gzip-compressed CSVs, comma-separated,
    standard UTF-8/ASCII (no dirty placeholders like the KTAS CSV had).
  - edstays: 222 rows, columns exactly:
      subject_id, hadm_id, stay_id, intime, outtime, gender, race,
      arrival_transport, disposition
  - triage: 222 rows (one per stay, confirmed 1:1 join with edstays,
    zero orphans either direction), columns exactly:
      subject_id, stay_id, temperature, heartrate, resprate, o2sat,
      sbp, dbp, pain, acuity, chiefcomplaint
  - vitalsign: 1038 rows across 206 distinct stay_ids (NOT one row per
    stay -- this is repeated in-ED monitoring, up to 22 readings for a
    single stay, taken at various charttimes after intime, sometimes
    hours later). This table is NOT used as a triage-time input for
    that reason -- see LEAKAGE POLICY below.
  - diagnosis: 545 rows, ICD-9 and ICD-10 codes both present, assigned
    in the ED (i.e. after clinical assessment -- retrospective).
  - medrecon: 2764 rows, home medications reported on arrival, near
    intime but excluded from triage-time input by deliberate choice
    (matches the existing RETROSPECTIVE_OR_LEAKAGE_COLUMNS classification
    in app/schemas/mimic_ed.py, which predates this adapter).
  - pyxis: 1082 rows, medications administered during the ED visit --
    unambiguously retrospective (a consequence of treatment, not an
    input to triage).

KNOWN DATA QUALITY ISSUE FOUND DURING INSPECTION (not invented, not
silently corrected): triage.temperature has a minimum value of 36.5,
which is implausible as a Fahrenheit body temperature (it would
indicate severe hypothermia) but plausible as Celsius. This is most
likely a single data-entry error in the source CSV. This adapter does
NOT guess a corrected value or silently convert it -- the value is kept
exactly as recorded, and flagged in scripts/audit_mimic_demo.py's output
(data/processed/mimic_demo_audit_report.json) so a human can decide what
to do with it. (No MIMIC-specific label-builder script exists yet --
unlike scripts/build_ktas_labels.py for KTAS, MIMIC-specific ML/label
work has not been started. If one is built later, e.g. as
scripts/build_mimic_outcome_labels.py, this same finding should be
re-surfaced there too.)

KNOWN DIRTY VALUES FOUND IN triage.pain (not assumed, found by directly
inspecting the value_counts of the real column): alongside genuine 0-10
numeric pain scores, the column contains an out-of-range "13" and
non-numeric junk strings ("unable", "UA", "Critical", "o", "uta", "ett").
_parse_pain() below treats anything outside 0-10 (after attempting a
numeric parse) as unparseable/missing, matching the same defensive
pattern already used in app/rules/manchester_engine.py's own pain parser.

LEAKAGE POLICY -- enforced by this adapter, consistent with the existing
RETROSPECTIVE_OR_LEAKAGE_COLUMNS in app/schemas/mimic_ed.py:
  - triage.acuity, edstays.disposition, edstays.outtime, edstays.hadm_id,
    and every diagnosis/medrecon/pyxis record are placed ONLY in
    RetrospectiveLabels (via EDTriageCase.retrospective_metadata and the
    diagnoses/medrecon/pyxis lists), never in TriageTimeInput.
  - vitalsign.csv rows are NOT used as triage-time vitals (see above);
    they are stored in EDTriageCase.vitals_timeseries for potential
    future research use, clearly separated from triage.csv's single
    triage-time snapshot.
  - This adapter does not assign, infer, or guess a Manchester Triage
    System category from any MIMIC field. acuity is the MIMIC nurse's
    own triage acuity (1-5 scale, distinct from KTAS and from Manchester)
    and is kept as a research label only.
"""
from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from app.schemas.internal import (
    EDTriageCase,
    EDStaySource,
    TriageSource,
    VitalSignRecord,
    DiagnosisRecord,
    MedReconRecord,
    PyxisRecord,
)

SOURCE_DATASET_LABEL = "MIMIC-IV-ED-Demo-v2.2"

# Verified directly against the real uploaded zip's ed/ directory.
EXPECTED_TABLES = ["diagnosis", "edstays", "medrecon", "pyxis", "triage", "vitalsign"]

EXPECTED_COLUMNS = {
    "edstays": ["subject_id", "hadm_id", "stay_id", "intime", "outtime",
                "gender", "race", "arrival_transport", "disposition"],
    "triage": ["subject_id", "stay_id", "temperature", "heartrate", "resprate",
               "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint"],
    "vitalsign": ["subject_id", "stay_id", "charttime", "temperature", "heartrate",
                  "resprate", "o2sat", "sbp", "dbp", "rhythm", "pain"],
    "diagnosis": ["subject_id", "stay_id", "seq_num", "icd_code", "icd_version", "icd_title"],
    "medrecon": ["subject_id", "stay_id", "charttime", "name", "gsn", "ndc",
                 "etc_rn", "etccode", "etcdescription"],
    "pyxis": ["subject_id", "stay_id", "charttime", "med_rn", "name", "gsn_rn", "gsn"],
}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if pd.isna(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    f = _to_float(value)
    return int(f) if f is not None else None


def _to_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s if s else None


def _parse_pain(value: Any) -> Optional[str]:
    """
    Parses triage.pain, which is genuinely dirty in this dataset (verified
    directly, not assumed): contains valid 0-10 scores, an out-of-range
    "13", and non-numeric junk ("unable", "UA", "Critical", "o", "uta",
    "ett"). Returns the original numeric string if it parses as a valid
    0-10 score, otherwise None (treated as missing/unparseable, not
    coerced into a number it doesn't represent).
    """
    s = _to_str(value)
    if s is None:
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    if 0 <= f <= 10:
        return s
    return None


def load_mimic_table(ed_dir: Path, table_name: str) -> pd.DataFrame:
    """Loads one table by name from a directory containing the six
    standard .csv.gz files (e.g. .../mimic-iv-ed-demo-2.2/ed/)."""
    path = ed_dir / f"{table_name}.csv.gz"
    if not path.exists():
        raise FileNotFoundError(
            f"Expected MIMIC table not found: {path}\n"
            f"Looked for {table_name}.csv.gz under {ed_dir}"
        )
    with gzip.open(path, "rt") as f:
        df = pd.read_csv(f)
    return df


def validate_mimic_tables(ed_dir: Path) -> dict:
    """
    Loads all six tables and checks their columns against EXPECTED_COLUMNS.
    Returns a report dict; does not raise on mismatch (the caller decides
    whether a mismatch is fatal), so this can be used purely for auditing.
    """
    report: dict[str, Any] = {"tables": {}, "all_columns_match": True}
    for table_name in EXPECTED_TABLES:
        try:
            df = load_mimic_table(ed_dir, table_name)
        except FileNotFoundError as exc:
            report["tables"][table_name] = {"status": "MISSING", "error": str(exc)}
            report["all_columns_match"] = False
            continue
        actual_cols = list(df.columns)
        expected_cols = EXPECTED_COLUMNS[table_name]
        cols_match = actual_cols == expected_cols
        if not cols_match:
            report["all_columns_match"] = False
        report["tables"][table_name] = {
            "status": "OK" if cols_match else "COLUMN_MISMATCH",
            "row_count": len(df),
            "expected_columns": expected_cols,
            "actual_columns": actual_cols,
        }
    return report


def dataframe_to_cases(
    edstays_df: pd.DataFrame,
    triage_df: pd.DataFrame,
    vitalsign_df: pd.DataFrame,
    diagnosis_df: pd.DataFrame,
    medrecon_df: pd.DataFrame,
    pyxis_df: pd.DataFrame,
    n: Optional[int] = None,
) -> List[EDTriageCase]:
    """
    Joins the six MIMIC tables into a list of EDTriageCase objects, one per
    ED stay (keyed on stay_id from edstays, since that is the table every
    other table's stay_id is verified to be a subset of or equal to).
    """
    cases: List[EDTriageCase] = []

    triage_by_stay = {int(r["stay_id"]): r for _, r in triage_df.iterrows()}
    vitals_by_stay: dict[int, list] = {}
    for _, r in vitalsign_df.iterrows():
        vitals_by_stay.setdefault(int(r["stay_id"]), []).append(r)
    diagnosis_by_stay: dict[int, list] = {}
    for _, r in diagnosis_df.iterrows():
        diagnosis_by_stay.setdefault(int(r["stay_id"]), []).append(r)
    medrecon_by_stay: dict[int, list] = {}
    for _, r in medrecon_df.iterrows():
        medrecon_by_stay.setdefault(int(r["stay_id"]), []).append(r)
    pyxis_by_stay: dict[int, list] = {}
    for _, r in pyxis_df.iterrows():
        pyxis_by_stay.setdefault(int(r["stay_id"]), []).append(r)

    rows = edstays_df.itertuples(index=False)
    for i, row in enumerate(rows):
        if n is not None and i >= n:
            break

        row_d = row._asdict()
        stay_id = _to_int(row_d["stay_id"])
        subject_id = _to_int(row_d["subject_id"])

        edstay = EDStaySource(
            subject_id=subject_id,
            hadm_id=_to_int(row_d.get("hadm_id")),
            stay_id=stay_id,
            intime=_to_str(row_d.get("intime")),
            outtime=_to_str(row_d.get("outtime")),
            gender=_to_str(row_d.get("gender")),
            race=_to_str(row_d.get("race")),
            arrival_transport=_to_str(row_d.get("arrival_transport")),
            disposition=_to_str(row_d.get("disposition")),
        )

        triage_row = triage_by_stay.get(stay_id)
        triage_source: Optional[TriageSource] = None
        retro_acuity: Optional[float] = None
        if triage_row is not None:
            retro_acuity = _to_float(triage_row.get("acuity"))
            triage_source = TriageSource(
                subject_id=subject_id,
                stay_id=stay_id,
                temperature=_to_float(triage_row.get("temperature")),
                temperature_unit="F",  # MIMIC-IV-ED records temperature in Fahrenheit
                heartrate=_to_float(triage_row.get("heartrate")),
                resprate=_to_float(triage_row.get("resprate")),
                o2sat=_to_float(triage_row.get("o2sat")),
                sbp=_to_float(triage_row.get("sbp")),
                dbp=_to_float(triage_row.get("dbp")),
                pain=_parse_pain(triage_row.get("pain")),
                chiefcomplaint=_to_str(triage_row.get("chiefcomplaint")),
                acuity=retro_acuity,  # stored on TriageSource for to_retrospective_labels() to read;
                                       # to_triage_time_input() does NOT expose this field -- verified
                                       # in app/schemas/internal.py's own to_triage_time_input()
            )

        vitals_records = [
            VitalSignRecord(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime=_to_str(v.get("charttime")),
                temperature=_to_float(v.get("temperature")),
                temperature_unit="F",
                heartrate=_to_float(v.get("heartrate")),
                resprate=_to_float(v.get("resprate")),
                o2sat=_to_float(v.get("o2sat")),
                sbp=_to_float(v.get("sbp")),
                dbp=_to_float(v.get("dbp")),
                rhythm=_to_str(v.get("rhythm")),
                pain=_parse_pain(v.get("pain")),
            )
            for v in vitals_by_stay.get(stay_id, [])
        ]

        diagnosis_records = [
            DiagnosisRecord(
                subject_id=subject_id,
                stay_id=stay_id,
                seq_num=_to_int(d.get("seq_num")),
                icd_code=_to_str(d.get("icd_code")),
                icd_version=_to_int(d.get("icd_version")),
                icd_title=_to_str(d.get("icd_title")),
            )
            for d in diagnosis_by_stay.get(stay_id, [])
        ]

        medrecon_records = [
            MedReconRecord(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime=_to_str(m.get("charttime")),
                name=_to_str(m.get("name")),
                gsn=_to_str(m.get("gsn")),
                ndc=_to_str(m.get("ndc")),
                etc_rn=_to_int(m.get("etc_rn")),
                etccode=_to_str(m.get("etccode")),
                etcdescription=_to_str(m.get("etcdescription")),
            )
            for m in medrecon_by_stay.get(stay_id, [])
        ]

        pyxis_records = [
            PyxisRecord(
                subject_id=subject_id,
                stay_id=stay_id,
                charttime=_to_str(p.get("charttime")),
                med_rn=_to_int(p.get("med_rn")),
                name=_to_str(p.get("name")),
                gsn_rn=_to_int(p.get("gsn_rn")),
                gsn=_to_str(p.get("gsn")),
            )
            for p in pyxis_by_stay.get(stay_id, [])
        ]

        case = EDTriageCase(
            source_dataset=SOURCE_DATASET_LABEL,
            stay_id=stay_id,
            subject_id=subject_id,
            edstay=edstay,
            triage=triage_source,
            vitals_timeseries=vitals_records,
            diagnoses=diagnosis_records,
            medrecon=medrecon_records,
            pyxis=pyxis_records,
            retrospective_metadata={
                # MIMIC has no KTAS-style fields; this dict intentionally
                # stays empty of ktas_* keys so to_retrospective_labels()
                # correctly returns None for all of them via meta.get(...).
            },
        )
        cases.append(case)

    return cases


def load_mimic_demo_cases(
    ed_dir: Path, n: Optional[int] = None
) -> tuple[List[EDTriageCase], dict]:
    """
    Top-level entry point: loads all six tables from ed_dir and returns
    (cases, validation_report). Raises FileNotFoundError if any required
    table is missing -- this is a hard requirement, unlike
    validate_mimic_tables() which is purely diagnostic.
    """
    validation_report = validate_mimic_tables(ed_dir)
    if not validation_report["all_columns_match"]:
        missing = [
            t for t, info in validation_report["tables"].items()
            if info["status"] == "MISSING"
        ]
        if missing:
            raise FileNotFoundError(
                f"Required MIMIC table(s) missing under {ed_dir}: {missing}"
            )
        # Column mismatch (not missing file) is not fatal -- caller can
        # inspect validation_report and decide. This keeps the adapter
        # honest about a schema change in a future MIMIC release without
        # silently breaking.

    edstays_df = load_mimic_table(ed_dir, "edstays")
    triage_df = load_mimic_table(ed_dir, "triage")
    vitalsign_df = load_mimic_table(ed_dir, "vitalsign")
    diagnosis_df = load_mimic_table(ed_dir, "diagnosis")
    medrecon_df = load_mimic_table(ed_dir, "medrecon")
    pyxis_df = load_mimic_table(ed_dir, "pyxis")

    cases = dataframe_to_cases(
        edstays_df, triage_df, vitalsign_df, diagnosis_df, medrecon_df, pyxis_df, n=n
    )
    return cases, validation_report
