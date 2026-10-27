"""Auditable evaluation of the existing provisional vital safety thresholds.

These are project safety checks, not the licensed MTS discriminator catalogue.
The same definitions drive the override and its displayed evidence. Missing
observations are never represented as a negative discriminator assessment.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import operator

from app.rules.vitals import temperature_c
from app.schemas.internal import TriageTimeInput


@dataclass(frozen=True)
class VitalDiscriminator:
    code: str
    label: str
    field: str
    unit: str
    operator: str
    threshold: float
    priority: int


# Preserve the existing thresholds and flag ordering. This is not a clinical
# policy change, and the rule version remains acuity_override_v1.
VITAL_DISCRIMINATORS = (
    VitalDiscriminator("EXTREME_HYPOXIA_SPO2_BELOW_85", "Very low oxygen saturation", "o2sat", "%", "<", 85, 1),
    VitalDiscriminator("EXTREME_TACHYCARDIA_HR_ABOVE_150", "Very high heart rate", "heartrate", "bpm", ">", 150, 1),
    VitalDiscriminator("EXTREME_BRADYCARDIA_HR_BELOW_35", "Very low heart rate", "heartrate", "bpm", "<", 35, 1),
    VitalDiscriminator("EXTREME_TACHYPNOEA_RR_ABOVE_35", "Very high respiratory rate", "resprate", "/min", ">", 35, 1),
    VitalDiscriminator("EXTREME_BRADYPNOEA_RR_BELOW_6", "Very low respiratory rate", "resprate", "/min", "<", 6, 1),
    VitalDiscriminator("EXTREME_HYPOTENSION_SBP_BELOW_80", "Very low systolic pressure", "sbp", "mmHg", "<", 80, 1),
    VitalDiscriminator("EXTREME_HYPERPYREXIA_TEMP_ABOVE_41_5C", "Very high temperature", "temperature", "C", ">=", 41.5, 1),
    VitalDiscriminator("EXTREME_HYPOTHERMIA_TEMP_BELOW_32C", "Very low temperature", "temperature", "C", "<", 32, 1),
    VitalDiscriminator("CRITICAL_HYPOXIA_SPO2_BELOW_90", "Low oxygen saturation", "o2sat", "%", "<", 90, 2),
    VitalDiscriminator("CRITICAL_HEART_RATE_ABOVE_130", "High heart rate", "heartrate", "bpm", ">", 130, 2),
    VitalDiscriminator("CRITICAL_HEART_RATE_BELOW_40", "Low heart rate", "heartrate", "bpm", "<", 40, 2),
    VitalDiscriminator("CRITICAL_RESPIRATORY_RATE_ABOVE_29", "High respiratory rate", "resprate", "/min", ">", 29, 2),
    VitalDiscriminator("CRITICAL_RESPIRATORY_RATE_BELOW_8", "Low respiratory rate", "resprate", "/min", "<", 8, 2),
    VitalDiscriminator("CRITICAL_HYPOTENSION_SBP_BELOW_90", "Low systolic pressure", "sbp", "mmHg", "<", 90, 2),
    VitalDiscriminator("CRITICAL_HYPERTENSION_SBP_ABOVE_220", "High systolic pressure", "sbp", "mmHg", ">", 220, 2),
    VitalDiscriminator("CRITICAL_HYPERPYREXIA_TEMP_ABOVE_41C", "High temperature", "temperature", "C", ">=", 41, 2),
    VitalDiscriminator("CRITICAL_HYPOTHERMIA_TEMP_BELOW_35C", "Low temperature", "temperature", "C", "<", 35, 2),
)
_OPERATORS = {"<": operator.lt, ">": operator.gt, ">=": operator.ge}


def evaluate_vital_discriminators(triage_input: TriageTimeInput) -> list[dict]:
    results = []
    for rule in VITAL_DISCRIMINATORS:
        value = (temperature_c(triage_input) if rule.field == "temperature"
                 else getattr(triage_input, rule.field))
        if value is None or not math.isfinite(value):
            value, status = None, "not_assessed"
        else:
            status = ("matched" if _OPERATORS[rule.operator](value, rule.threshold)
                      else "not_matched")
        results.append({
            "code": rule.code, "label": rule.label, "field": rule.field,
            "observed_value": value, "unit": rule.unit,
            "criterion": f"{rule.operator} {rule.threshold:g} {rule.unit}",
            "operator": rule.operator, "threshold": rule.threshold,
            "priority": rule.priority,
            "tier": "EXTREME" if rule.priority == 1 else "CRITICAL",
            "status": status,
        })
    return results


def build_discriminator_evidence(evaluated: list[dict], *, override_applied: bool,
                                 model_policy: dict, pathway: dict) -> dict:
    matched = [item for item in evaluated if item["status"] == "matched"]
    floor = min((item["priority"] for item in matched), default=None)
    return {
        "status": "EVALUATED", "source": "PROVISIONAL_VITAL_SAFETY_RULES",
        "rule_version": "acuity_override_v1",
        "is_official_mts": False, "is_clinically_approved": False,
        "safety_floor_acuity": floor, "override_applied": override_applied,
        "decisive": [item for item in matched
                     if override_applied and item["priority"] == floor],
        "matched": matched, "evaluated": evaluated,
        "missing_observations": sorted({item["field"] for item in evaluated
                                        if item["status"] == "not_assessed"}),
        "model_policy": model_policy, "pathway": pathway,
    }
