from app.schemas.mimic_ed import EXPECTED_COLUMNS


def test_six_expected_tables_exist():
    assert set(EXPECTED_COLUMNS) == {
        "edstays",
        "triage",
        "vitalsign",
        "diagnosis",
        "medrecon",
        "pyxis",
    }


def test_triage_expected_columns_exact():
    assert EXPECTED_COLUMNS["triage"] == [
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
    ]
