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
    temperature: Optional[float] = None
    heartrate: Optional[float] = None
    resprate: Optional[float] = None
    o2sat: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    pain: Optional[str] = None
    acuity: Optional[float] = None
    chiefcomplaint: Optional[str] = None


class VitalSignRecord(BaseModel):
    subject_id: int
    stay_id: int
    charttime: Optional[str] = None
    temperature: Optional[float] = None
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
    source_dataset: str = "MIMIC-IV-ED Demo v2.2"
    subject_id: int
    stay_id: int
    intime: Optional[str] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    arrival_transport: Optional[str] = None
    chiefcomplaint: Optional[str] = None
    temperature: Optional[float] = None
    heartrate: Optional[float] = None
    resprate: Optional[float] = None
    o2sat: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    pain: Optional[str] = None


class RetrospectiveLabels(BaseModel):
    original_acuity: Optional[float] = None
    disposition: Optional[str] = None
    outtime: Optional[str] = None
    diagnoses: List[DiagnosisRecord] = Field(default_factory=list)


class EDTriageCase(BaseModel):
    source_dataset: str = "MIMIC-IV-ED Demo v2.2"
    stay_id: int
    subject_id: int

    edstay: EDStaySource
    triage: Optional[TriageSource] = None
    vitals_timeseries: List[VitalSignRecord] = Field(default_factory=list)
    diagnoses: List[DiagnosisRecord] = Field(default_factory=list)
    medrecon: List[MedReconRecord] = Field(default_factory=list)
    pyxis: List[PyxisRecord] = Field(default_factory=list)

    audit_metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_triage_time_input(self) -> TriageTimeInput:
        return TriageTimeInput(
            subject_id=self.subject_id,
            stay_id=self.stay_id,
            intime=self.edstay.intime,
            gender=self.edstay.gender,
            race=self.edstay.race,
            arrival_transport=self.edstay.arrival_transport,
            chiefcomplaint=self.triage.chiefcomplaint if self.triage else None,
            temperature=self.triage.temperature if self.triage else None,
            heartrate=self.triage.heartrate if self.triage else None,
            resprate=self.triage.resprate if self.triage else None,
            o2sat=self.triage.o2sat if self.triage else None,
            sbp=self.triage.sbp if self.triage else None,
            dbp=self.triage.dbp if self.triage else None,
            pain=self.triage.pain if self.triage else None,
        )

    def to_retrospective_labels(self) -> RetrospectiveLabels:
        return RetrospectiveLabels(
            original_acuity=self.triage.acuity if self.triage else None,
            disposition=self.edstay.disposition,
            outtime=self.edstay.outtime,
            diagnoses=self.diagnoses,
        )
