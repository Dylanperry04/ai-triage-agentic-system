from __future__ import annotations

from typing import Dict, List, Any
import math
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


def clean_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def row_to_dict(row: pd.Series) -> Dict[str, Any]:
    return {k: clean_value(v) for k, v in row.to_dict().items()}


def as_optional_int(value):
    value = clean_value(value)
    if value is None:
        return None
    return int(value)


def as_optional_float(value):
    value = clean_value(value)
    if value is None:
        return None
    return float(value)


def as_optional_str(value):
    value = clean_value(value)
    if value is None:
        return None
    return str(value)


def build_cases(tables: Dict[str, pd.DataFrame], n: int | None = None) -> List[EDTriageCase]:
    edstays = tables["edstays"].copy()
    triage = tables["triage"].copy()
    vitalsign = tables["vitalsign"].copy()
    diagnosis = tables["diagnosis"].copy()
    medrecon = tables["medrecon"].copy()
    pyxis = tables["pyxis"].copy()

    if n is not None:
        edstays = edstays.sort_values("stay_id").head(n).copy()

    selected_stay_ids = set(edstays["stay_id"].astype(int).tolist())

    triage_by_stay = {
        int(row["stay_id"]): row
        for _, row in triage[triage["stay_id"].isin(selected_stay_ids)].iterrows()
    }

    vitals_by_stay = {
        int(stay_id): group
        for stay_id, group in vitalsign[vitalsign["stay_id"].isin(selected_stay_ids)].groupby("stay_id")
    }

    diagnosis_by_stay = {
        int(stay_id): group
        for stay_id, group in diagnosis[diagnosis["stay_id"].isin(selected_stay_ids)].groupby("stay_id")
    }

    medrecon_by_stay = {
        int(stay_id): group
        for stay_id, group in medrecon[medrecon["stay_id"].isin(selected_stay_ids)].groupby("stay_id")
    }

    pyxis_by_stay = {
        int(stay_id): group
        for stay_id, group in pyxis[pyxis["stay_id"].isin(selected_stay_ids)].groupby("stay_id")
    }

    cases: List[EDTriageCase] = []

    for _, stay_row in edstays.sort_values("stay_id").iterrows():
        stay = row_to_dict(stay_row)
        stay_id = int(stay["stay_id"])
        subject_id = int(stay["subject_id"])

        edstay_source = EDStaySource(
            subject_id=subject_id,
            hadm_id=as_optional_int(stay.get("hadm_id")),
            stay_id=stay_id,
            intime=as_optional_str(stay.get("intime")),
            outtime=as_optional_str(stay.get("outtime")),
            gender=as_optional_str(stay.get("gender")),
            race=as_optional_str(stay.get("race")),
            arrival_transport=as_optional_str(stay.get("arrival_transport")),
            disposition=as_optional_str(stay.get("disposition")),
        )

        triage_source = None
        if stay_id in triage_by_stay:
            tr = row_to_dict(triage_by_stay[stay_id])
            triage_source = TriageSource(
                subject_id=int(tr["subject_id"]),
                stay_id=int(tr["stay_id"]),
                temperature=as_optional_float(tr.get("temperature")),
                heartrate=as_optional_float(tr.get("heartrate")),
                resprate=as_optional_float(tr.get("resprate")),
                o2sat=as_optional_float(tr.get("o2sat")),
                sbp=as_optional_float(tr.get("sbp")),
                dbp=as_optional_float(tr.get("dbp")),
                pain=as_optional_str(tr.get("pain")),
                acuity=as_optional_float(tr.get("acuity")),
                chiefcomplaint=as_optional_str(tr.get("chiefcomplaint")),
            )

        vital_records: List[VitalSignRecord] = []
        if stay_id in vitals_by_stay:
            for _, r in vitals_by_stay[stay_id].sort_values("charttime").iterrows():
                d = row_to_dict(r)
                vital_records.append(VitalSignRecord(
                    subject_id=int(d["subject_id"]),
                    stay_id=int(d["stay_id"]),
                    charttime=as_optional_str(d.get("charttime")),
                    temperature=as_optional_float(d.get("temperature")),
                    heartrate=as_optional_float(d.get("heartrate")),
                    resprate=as_optional_float(d.get("resprate")),
                    o2sat=as_optional_float(d.get("o2sat")),
                    sbp=as_optional_float(d.get("sbp")),
                    dbp=as_optional_float(d.get("dbp")),
                    rhythm=as_optional_str(d.get("rhythm")),
                    pain=as_optional_str(d.get("pain")),
                ))

        diagnosis_records: List[DiagnosisRecord] = []
        if stay_id in diagnosis_by_stay:
            for _, r in diagnosis_by_stay[stay_id].sort_values("seq_num").iterrows():
                d = row_to_dict(r)
                diagnosis_records.append(DiagnosisRecord(
                    subject_id=int(d["subject_id"]),
                    stay_id=int(d["stay_id"]),
                    seq_num=as_optional_int(d.get("seq_num")),
                    icd_code=as_optional_str(d.get("icd_code")),
                    icd_version=as_optional_int(d.get("icd_version")),
                    icd_title=as_optional_str(d.get("icd_title")),
                ))

        medrecon_records: List[MedReconRecord] = []
        if stay_id in medrecon_by_stay:
            for _, r in medrecon_by_stay[stay_id].sort_values("charttime").iterrows():
                d = row_to_dict(r)
                medrecon_records.append(MedReconRecord(
                    subject_id=int(d["subject_id"]),
                    stay_id=int(d["stay_id"]),
                    charttime=as_optional_str(d.get("charttime")),
                    name=as_optional_str(d.get("name")),
                    gsn=as_optional_str(d.get("gsn")),
                    ndc=as_optional_str(d.get("ndc")),
                    etc_rn=as_optional_int(d.get("etc_rn")),
                    etccode=as_optional_str(d.get("etccode")),
                    etcdescription=as_optional_str(d.get("etcdescription")),
                ))

        pyxis_records: List[PyxisRecord] = []
        if stay_id in pyxis_by_stay:
            for _, r in pyxis_by_stay[stay_id].sort_values("charttime").iterrows():
                d = row_to_dict(r)
                pyxis_records.append(PyxisRecord(
                    subject_id=int(d["subject_id"]),
                    stay_id=int(d["stay_id"]),
                    charttime=as_optional_str(d.get("charttime")),
                    med_rn=as_optional_int(d.get("med_rn")),
                    name=as_optional_str(d.get("name")),
                    gsn_rn=as_optional_int(d.get("gsn_rn")),
                    gsn=as_optional_str(d.get("gsn")),
                ))

        cases.append(EDTriageCase(
            stay_id=stay_id,
            subject_id=subject_id,
            edstay=edstay_source,
            triage=triage_source,
            vitals_timeseries=vital_records,
            diagnoses=diagnosis_records,
            medrecon=medrecon_records,
            pyxis=pyxis_records,
            audit_metadata={
                "source_tables_used": ["edstays", "triage", "vitalsign", "diagnosis", "medrecon", "pyxis"],
                "triage_input_policy": "Only triage-time fields exposed via to_triage_time_input()",
                "retrospective_fields_policy": "Acuity, disposition, diagnoses, repeated vitals, medrecon, and pyxis preserved separately",
            },
        ))

    return cases
