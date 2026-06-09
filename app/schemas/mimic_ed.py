from typing import Dict, List


MIMIC_ED_DEMO_BASE_URL = "https://physionet.org/files/mimic-iv-ed-demo/2.2/ed"

MIMIC_ED_FILES: Dict[str, str] = {
    "edstays": "edstays.csv.gz",
    "triage": "triage.csv.gz",
    "vitalsign": "vitalsign.csv.gz",
    "diagnosis": "diagnosis.csv.gz",
    "medrecon": "medrecon.csv.gz",
    "pyxis": "pyxis.csv.gz",
}

EXPECTED_COLUMNS: Dict[str, List[str]] = {
    "edstays": [
        "subject_id",
        "hadm_id",
        "stay_id",
        "intime",
        "outtime",
        "gender",
        "race",
        "arrival_transport",
        "disposition",
    ],
    "triage": [
        "subject_id",
        "stay_id",
        "temperature",
        "heartrate",
        "resprate",
        "o2sat",
        "sbp",
        "dbp",
        "pain",
        "acuity",
        "chiefcomplaint",
    ],
    "vitalsign": [
        "subject_id",
        "stay_id",
        "charttime",
        "temperature",
        "heartrate",
        "resprate",
        "o2sat",
        "sbp",
        "dbp",
        "rhythm",
        "pain",
    ],
    "diagnosis": [
        "subject_id",
        "stay_id",
        "seq_num",
        "icd_code",
        "icd_version",
        "icd_title",
    ],
    "medrecon": [
        "subject_id",
        "stay_id",
        "charttime",
        "name",
        "gsn",
        "ndc",
        "etc_rn",
        "etccode",
        "etcdescription",
    ],
    "pyxis": [
        "subject_id",
        "stay_id",
        "charttime",
        "med_rn",
        "name",
        "gsn_rn",
        "gsn",
    ],
}


TRIAGE_INPUT_COLUMNS = [
    "subject_id",
    "stay_id",
    "intime",
    "gender",
    "race",
    "arrival_transport",
    "chiefcomplaint",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
]


RETROSPECTIVE_OR_LEAKAGE_COLUMNS = [
    "outtime",
    "disposition",
    "acuity",
    "diagnoses",
    "vitals_timeseries",
    "medrecon",
    "pyxis",
]
