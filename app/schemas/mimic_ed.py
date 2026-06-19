"""
MIMIC-IV-ED Schema Constants.

These column lists are the verified ground truth for both:
  - mimic-iv-ed-demo/2.2  (public, 100 patients, no credentials)
  - mimic-iv-ed/2.2       (full dataset, ~216k stays, requires PhysioNet access)

The full dataset has the same six-table structure and the same column headers.
This file is the single place to update if PhysioNet releases a new version.

LEAKAGE POLICY
--------------
Retrospective / outcome fields must NEVER enter the triage-time workflow.
They exist in the source tables but are stripped at the mapping stage.
"""

from typing import Dict, List

# ── Download URLs ─────────────────────────────────────────────────────────────

MIMIC_ED_DEMO_BASE_URL = "https://physionet.org/files/mimic-iv-ed-demo/2.2/ed"
MIMIC_ED_FULL_BASE_URL = "https://physionet.org/files/mimic-iv-ed/2.2/ed"

MIMIC_ED_FILES: Dict[str, str] = {
    "edstays":   "edstays.csv.gz",
    "triage":    "triage.csv.gz",
    "vitalsign": "vitalsign.csv.gz",
    "diagnosis": "diagnosis.csv.gz",
    "medrecon":  "medrecon.csv.gz",
    "pyxis":     "pyxis.csv.gz",
}

# ── Exact expected column headers (verified against v2.2) ─────────────────────

EXPECTED_COLUMNS: Dict[str, List[str]] = {
    "edstays": [
        "subject_id", "hadm_id", "stay_id", "intime", "outtime",
        "gender", "race", "arrival_transport", "disposition",
    ],
    "triage": [
        "subject_id", "stay_id", "temperature", "heartrate", "resprate",
        "o2sat", "sbp", "dbp", "pain", "acuity", "chiefcomplaint",
    ],
    "vitalsign": [
        "subject_id", "stay_id", "charttime", "temperature", "heartrate",
        "resprate", "o2sat", "sbp", "dbp", "rhythm", "pain",
    ],
    "diagnosis": [
        "subject_id", "stay_id", "seq_num", "icd_code", "icd_version", "icd_title",
    ],
    "medrecon": [
        "subject_id", "stay_id", "charttime", "name", "gsn", "ndc",
        "etc_rn", "etccode", "etcdescription",
    ],
    "pyxis": [
        "subject_id", "stay_id", "charttime", "med_rn", "name", "gsn_rn", "gsn",
    ],
}

# ── Fields allowed into triage-time workflow ──────────────────────────────────
# These are the ONLY fields from the source tables that may enter the
# TriageTimeInput schema. Everything else is retrospective/leakage.

TRIAGE_INPUT_COLUMNS: List[str] = [
    "subject_id",
    "stay_id",
    "intime",          # arrival time — available at triage
    "gender",          # demographic — available at triage
    "race",            # demographic — available at triage
    "arrival_transport",  # ambulance/walk-in — available at triage
    "chiefcomplaint",  # nurse-recorded chief complaint at triage
    "temperature",     # triage-time vital
    "heartrate",       # triage-time vital
    "resprate",        # triage-time vital
    "o2sat",           # triage-time vital
    "sbp",             # triage-time vital
    "dbp",             # triage-time vital
    "pain",            # triage-time pain score (string in MIMIC)
]

# ── Fields that must NEVER enter triage-time workflow ────────────────────────
# These are outcome/retrospective fields. Using them as triage inputs would
# constitute label leakage and produce dangerously overoptimistic models.

RETROSPECTIVE_OR_LEAKAGE_COLUMNS: List[str] = [
    "outtime",        # when patient left ED — unknown at triage
    "disposition",    # admitted/discharged — unknown at triage
    "acuity",         # MIMIC original triage acuity — used as training label ONLY
    "diagnoses",      # final diagnoses — unknown at triage
    "vitals_timeseries",  # subsequent vitals — unknown at triage
    "medrecon",       # medication reconciliation — unknown at triage
    "pyxis",          # medication dispensing — unknown at triage
    "hadm_id",        # hospital admission ID — implies admitted (leakage)

    # Kaggle KTAS leakage/outcome/audit fields
    "KTAS_RN", "KTAS_expert", "mistriage", "Error_group", "Diagnosis in ED",
    "Disposition", "Length of stay_min", "KTAS duration_min",
    "ktas_rn", "ktas_expert", "mistriage_label", "error_group",
    "diagnosis_in_ed", "length_of_stay_min", "ktas_duration_min",
    "label_ktas_rn", "label_ktas_expert", "label_mistriage", "label_error_group",
]
