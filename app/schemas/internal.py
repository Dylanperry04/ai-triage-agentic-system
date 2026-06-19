"""
Internal Pydantic data models for the AI Triage system.

The active dataset phase is Kaggle KTAS. The schemas keep backward-compatible
MIMIC fields while adding explicit KTAS fields and temperature units. Outcome,
expert-triage, diagnosis, disposition, and mistriage fields are kept only in
RetrospectiveLabels and never flow into TriageTimeInput.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EDStaySource(BaseModel):
    subject_id: int
    hadm_id: Optional[int] = None
    stay_id: int
    intime: Optional[str] = None
    outtime: Optional[str] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    arrival_transport: Optional[str] = None
    disposition: Optional[str] = None


class TriageSource(BaseModel):
    subject_id: int
    stay_id: int

    # Shared/core triage-time fields
    temperature: Optional[float] = None
    temperature_unit: str = "F"
    heartrate: Optional[float] = None
    resprate: Optional[float] = None
    o2sat: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    pain: Optional[str] = None
    chiefcomplaint: Optional[str] = None

    # Kaggle KTAS triage-time fields
    age: Optional[float] = None
    group_code: Optional[int] = None
    group_label: Optional[str] = None
    patients_per_hour: Optional[float] = None
    arrival_mode_code: Optional[int] = None
    injury_code: Optional[int] = None
    injury_label: Optional[str] = None
    mental_code: Optional[int] = None
    mental_label: Optional[str] = None
    pain_present: Optional[int] = None
    nrs_pain: Optional[float] = None

    # MIMIC original acuity: retrospective/evaluation only, never as input
    acuity: Optional[float] = None


class VitalSignRecord(BaseModel):
    subject_id: int
    stay_id: int
    charttime: Optional[str] = None
    temperature: Optional[float] = None
    temperature_unit: str = "F"
    heartrate: Optional[float] = None
    resprate: Optional[float] = None
    o2sat: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    rhythm: Optional[str] = None
    pain: Optional[str] = None


class DiagnosisRecord(BaseModel):
    subject_id: int
    stay_id: int
    seq_num: Optional[int] = None
    icd_code: Optional[str] = None
    icd_version: Optional[int] = None
    icd_title: Optional[str] = None


class MedReconRecord(BaseModel):
    subject_id: int
    stay_id: int
    charttime: Optional[str] = None
    name: Optional[str] = None
    gsn: Optional[str] = None
    ndc: Optional[str] = None
    etc_rn: Optional[int] = None
    etccode: Optional[str] = None
    etcdescription: Optional[str] = None


class PyxisRecord(BaseModel):
    subject_id: int
    stay_id: int
    charttime: Optional[str] = None
    med_rn: Optional[int] = None
    name: Optional[str] = None
    gsn_rn: Optional[int] = None
    gsn: Optional[str] = None


class TriageTimeInput(BaseModel):
    """
    Triage-time input record.

    This contains only fields plausibly available at triage/registration in the
    current data source. It explicitly excludes KTAS_RN, KTAS_expert,
    mistriage, Error_group, Diagnosis in ED, Disposition, length of stay, and
    KTAS duration because those are labels, outcomes, audit fields, or
    post-hoc timing fields.
    """
    source_dataset: str = "Kaggle-KTAS"
    subject_id: int
    stay_id: int
    intime: Optional[str] = None

    gender: Optional[str] = None
    race: Optional[str] = None
    age: Optional[float] = None
    arrival_transport: Optional[str] = None
    arrival_mode_code: Optional[int] = None
    group_code: Optional[int] = None
    group_label: Optional[str] = None
    patients_per_hour: Optional[float] = None
    injury_code: Optional[int] = None
    injury_label: Optional[str] = None
    mental_code: Optional[int] = None
    mental_label: Optional[str] = None

    chiefcomplaint: Optional[str] = None
    temperature: Optional[float] = None
    temperature_unit: str = "F"
    heartrate: Optional[float] = None
    resprate: Optional[float] = None
    o2sat: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    pain: Optional[str] = None
    pain_present: Optional[int] = None
    nrs_pain: Optional[float] = None


class RetrospectiveLabels(BaseModel):
    """
    Retrospective labels and outcomes for evaluation/audit only.
    These fields are never model input features in the triage-support model.
    """
    original_acuity: Optional[float] = None
    disposition: Optional[str] = None
    outtime: Optional[str] = None
    diagnoses: List[DiagnosisRecord] = Field(default_factory=list)

    ktas_rn: Optional[int] = None
    ktas_expert: Optional[int] = None
    ktas_emergency: Optional[int] = None
    mistriage: Optional[int] = None
    mistriage_label: Optional[str] = None
    error_group: Optional[int] = None
    diagnosis_in_ed: Optional[str] = None
    length_of_stay_min: Optional[float] = None
    ktas_duration_min: Optional[float] = None
    disposition_code: Optional[int] = None
    disposition_label: Optional[str] = None


class EDTriageCase(BaseModel):
    """
    Complete ED stay container.

    For Kaggle KTAS, one CSV row is represented as one synthetic ED stay.
    For MIMIC, this still supports the original grouped table structure.
    """
    source_dataset: str = "Kaggle-KTAS"
    stay_id: int
    subject_id: int

    edstay: EDStaySource
    triage: Optional[TriageSource] = None
    vitals_timeseries: List[VitalSignRecord] = Field(default_factory=list)
    diagnoses: List[DiagnosisRecord] = Field(default_factory=list)
    medrecon: List[MedReconRecord] = Field(default_factory=list)
    pyxis: List[PyxisRecord] = Field(default_factory=list)

    audit_metadata: Dict[str, Any] = Field(default_factory=dict)
    retrospective_metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_triage_time_input(self) -> TriageTimeInput:
        tr = self.triage
        return TriageTimeInput(
            source_dataset=self.source_dataset,
            subject_id=self.subject_id,
            stay_id=self.stay_id,
            intime=self.edstay.intime,
            gender=self.edstay.gender,
            race=self.edstay.race,
            age=tr.age if tr else None,
            arrival_transport=self.edstay.arrival_transport,
            arrival_mode_code=tr.arrival_mode_code if tr else None,
            group_code=tr.group_code if tr else None,
            group_label=tr.group_label if tr else None,
            patients_per_hour=tr.patients_per_hour if tr else None,
            injury_code=tr.injury_code if tr else None,
            injury_label=tr.injury_label if tr else None,
            mental_code=tr.mental_code if tr else None,
            mental_label=tr.mental_label if tr else None,
            chiefcomplaint=tr.chiefcomplaint if tr else None,
            temperature=tr.temperature if tr else None,
            temperature_unit=tr.temperature_unit if tr else "F",
            heartrate=tr.heartrate if tr else None,
            resprate=tr.resprate if tr else None,
            o2sat=tr.o2sat if tr else None,
            sbp=tr.sbp if tr else None,
            dbp=tr.dbp if tr else None,
            pain=tr.pain if tr else None,
            pain_present=tr.pain_present if tr else None,
            nrs_pain=tr.nrs_pain if tr else None,
        )

    def to_retrospective_labels(self) -> RetrospectiveLabels:
        meta = self.retrospective_metadata or {}
        tr = self.triage
        return RetrospectiveLabels(
            original_acuity=tr.acuity if tr else None,
            disposition=self.edstay.disposition,
            outtime=self.edstay.outtime,
            diagnoses=self.diagnoses,
            ktas_rn=meta.get("ktas_rn"),
            ktas_expert=meta.get("ktas_expert"),
            ktas_emergency=meta.get("ktas_emergency"),
            mistriage=meta.get("mistriage"),
            mistriage_label=meta.get("mistriage_label"),
            error_group=meta.get("error_group"),
            diagnosis_in_ed=meta.get("diagnosis_in_ed"),
            length_of_stay_min=meta.get("length_of_stay_min"),
            ktas_duration_min=meta.get("ktas_duration_min"),
            disposition_code=meta.get("disposition_code"),
            disposition_label=meta.get("disposition_label"),
        )
