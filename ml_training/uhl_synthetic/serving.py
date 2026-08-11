"""Fail-closed serving helpers for final UHL synthetic triage-vitals artefacts."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ml_training.uhl_synthetic.dataset import (
    DATASET_SOURCE,
    FEATURE_VALUE_RANGES,
    MODEL_INPUT_COLUMNS,
    OUT_OF_MODEL_SCOPE_COMPLAINTS,
    TRIAGE_TIMESTAMP_POLICY,
    TRIAGE_TIMEZONE,
)
from ml_training.uhl_synthetic.features import (
    ARTIFACT_CONTRACT_VERSION,
    RAW_INPUT_TYPE,
    season_from_month_value,
    time_bin_from_hour_value,
)

UHL_SYNTHETIC_DATASET_LABELS = {DATASET_SOURCE}

UHL_PRESENTING_COMPLAINTS = (
    "ABDOMINAL PAIN",
    "BACK PAIN",
    "BREATHING PROBLEM / SHORTNESS OF BREATH",
    "BURN",
    "CHEMICAL INJURY",
    "CHEST PAIN",
    "COLLAPSE / FAINT",
    "DELIBERATE SELF HARM / OVERDOSE",
    "DENTAL PROBLEM",
    "DIARRHOEA",
    "EAR PROBLEM",
    "EYE PROBLEM",
    "FIT / SEIZURE",
    "FOREIGN BODY",
    "INJURY",
    "INSECT BITES / STINGS",
    "INSECTS BITES / STINGS",
    "LIMB PAIN",
    "LIMB SWELLING",
    "LIMPING CHILD",
    "MAJOR EMERGENCY",
    "NASAL PROBLEM",
    "NECK PAIN",
    "NOSE BLEED",
    "NOT DISCLOSED",
    "OTHER",
    "PREGNANCY",
    "PSYCHIATRIC PROBLEM",
    "QUERY COVID-19",
    "RASH",
    "RECTAL BLEEDING",
    "SKIN INFECTION",
    "SORE THROAT",
    "TESTICULAR PAIN",
    "UNWELL ADULT",
    "UNWELL CHILD",
    "URINARY PROBLEM",
    "VAGINAL BLEEDING",
    "VOMITING",
    "VOMITING BLOOD",
    "WOUND",
)

_NORMALISED_COMPLAINTS = {
    re.sub(r"\s+", " ", value.strip().upper()): value
    for value in UHL_PRESENTING_COMPLAINTS
}


class UhlServingContractError(ValueError):
    """Raised when UHL serving inputs or artefacts are unsafe to use."""


def is_uhl_source_dataset(source_dataset: object) -> bool:
    return str(source_dataset or "").strip() in UHL_SYNTHETIC_DATASET_LABELS


def resolve_uhl_presenting_complaint(
    value: object,
    *,
    allowed_complaints: Iterable[str] | None = None,
) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    if not text:
        raise UhlServingContractError("presenting_complaint is required")
    if text in set(OUT_OF_MODEL_SCOPE_COMPLAINTS):
        raise UhlServingContractError(
            f"{text} is outside the ML model scope; apply the deterministic "
            "emergency/deceased pathway"
        )
    allowed = (
        set(_NORMALISED_COMPLAINTS)
        if allowed_complaints is None
        else {
            re.sub(r"\s+", " ", str(item or "").strip().upper())
            for item in allowed_complaints
        }
    )
    if text not in _NORMALISED_COMPLAINTS or text not in allowed:
        raise UhlServingContractError(
            "presenting_complaint is not an exact complaint category seen during fitting"
        )
    return _NORMALISED_COMPLAINTS[text]


def _parse_arrival_time(value: object) -> datetime:
    if value is None or str(value).strip() == "":
        raise UhlServingContractError(
            "arrival_time is required to derive UHL month/hour inputs"
        )
    try:
        parsed = pd.Timestamp(pd.to_datetime(str(value), errors="raise"))
        if parsed.tzinfo is None:
            parsed = parsed.tz_localize(
                TRIAGE_TIMEZONE,
                ambiguous=False,
                nonexistent="shift_forward",
            )
        else:
            parsed = parsed.tz_convert(ZoneInfo(TRIAGE_TIMEZONE))
    except Exception as exc:
        raise UhlServingContractError(
            f"arrival_time could not be parsed: {value!r}"
        ) from exc
    if pd.isna(parsed):
        raise UhlServingContractError(f"arrival_time could not be parsed: {value!r}")
    return parsed.to_pydatetime()


def time_bin_from_hour(hour: int) -> str:
    try:
        return time_bin_from_hour_value(hour)
    except (TypeError, ValueError):
        raise UhlServingContractError("hour must be in 0..23")


def season_from_month(month: int) -> str:
    try:
        return season_from_month_value(month)
    except (TypeError, ValueError):
        raise UhlServingContractError("month must be in 1..12")


def _coerce_required_numeric(item: object, field: str, *, low: float, high: float) -> float:
    raw = getattr(item, field, None)
    if raw is None or str(raw).strip() == "":
        raise UhlServingContractError(f"{field} is required")
    try:
        value = float(raw)
    except Exception as exc:
        raise UhlServingContractError(f"{field} is not numeric: {raw!r}") from exc
    if not np.isfinite(value) or value < low or value > high:
        raise UhlServingContractError(f"{field} must be finite and in {low:g}..{high:g}")
    return value


def _temperature_f(item: object) -> float:
    value = _coerce_required_numeric(item, "temperature", low=-100, high=250)
    unit = str(getattr(item, "temperature_unit", "F") or "F").strip().upper()
    if unit in {"F", "FAHRENHEIT"}:
        value_f = value
    elif unit in {"C", "CELSIUS", "CENTIGRADE"}:
        value_f = value * 9.0 / 5.0 + 32.0
    else:
        raise UhlServingContractError(f"temperature_unit is not supported: {unit!r}")
    low, high = FEATURE_VALUE_RANGES["temperature"]
    if value_f < low or value_f > high:
        raise UhlServingContractError(
            f"temperature must be in {low:g}..{high:g} Fahrenheit after conversion"
        )
    return value_f


def uhl_dataframe_from_triage_inputs(
    inputs: Iterable[object],
    *,
    allowed_complaints: Iterable[str] | None = None,
    fitted_age_support: dict | None = None,
) -> pd.DataFrame:
    rows = []
    for item in inputs:
        age = getattr(item, "age", None)
        if age is None:
            raise UhlServingContractError("age is required")
        try:
            age_value = float(age)
        except Exception as exc:
            raise UhlServingContractError(f"age is not numeric: {age!r}") from exc
        if not np.isfinite(age_value) or age_value < 0 or age_value > 130:
            raise UhlServingContractError("age must be finite and in 0..130")
        if fitted_age_support is not None:
            try:
                fitted_min = float(fitted_age_support["minimum"])
                fitted_max = float(fitted_age_support["maximum"])
            except Exception as exc:
                raise UhlServingContractError(
                    "artifact fitted_age_support is invalid"
                ) from exc
            if age_value < fitted_min or age_value > fitted_max:
                raise UhlServingContractError(
                    f"age {age_value:g} is outside fitted support "
                    f"{fitted_min:g}..{fitted_max:g}; route to deterministic review"
                )

        complaint = getattr(item, "presenting_complaint", None)
        complaint_code = resolve_uhl_presenting_complaint(
            complaint,
            allowed_complaints=allowed_complaints,
        )

        when = _parse_arrival_time(getattr(item, "arrival_time", None))
        month = int(when.month)
        hour = int(when.hour)
        vital_values = {
            field: _coerce_required_numeric(
                item,
                field,
                low=FEATURE_VALUE_RANGES[field][0],
                high=FEATURE_VALUE_RANGES[field][1],
            )
            for field in ("heartrate", "resprate", "o2sat", "sbp", "dbp", "pain")
        }
        rows.append({
            "age": age_value,
            "month": month,
            "hour": hour,
            "time_bin": time_bin_from_hour(hour),
            "season": season_from_month(month),
            "presenting_complaint": complaint_code,
            "temperature": _temperature_f(item),
            **vital_values,
        })
    return pd.DataFrame(rows, columns=MODEL_INPUT_COLUMNS)


DEPLOYABLE_RUN_STATUSES = {"COMPLETED"}


def deployability_failure_reason(bundle: object) -> str | None:
    """Why this artefact must not be promoted, or None when it may be.

    Contract v4 artefacts stamp the run's outcome into the bundle, so a
    preflight, failed, or incomplete run can be rejected from the artefact
    alone. Without this check a model produced by a run that exited non-zero
    could be copied to UHL_MODEL_PATH and served.
    """
    if not isinstance(bundle, dict):
        return "UHL artefact must be a joblib bundle dictionary"
    if bundle.get("preflight_mode") is True:
        return "artefact came from a preflight run and is not deployable"
    if bundle.get("deployment_eligible_run") is not True:
        return "artefact does not declare deployment_eligible_run=true"
    if bundle.get("test_safety_gate_passed") is not True:
        return "artefact did not pass the predeclared untouched-TEST safety gate"
    run_status = bundle.get("run_status")
    if run_status is None:
        return "artefact does not record run_status"
    if str(run_status) not in DEPLOYABLE_RUN_STATUSES:
        return f"artefact run_status is {run_status!r}, not COMPLETED"
    if bundle.get("candidate_failures"):
        count = len(bundle.get("candidate_failures") or [])
        return f"artefact came from a run with {count} failed candidate(s)"
    models_failed = bundle.get("models_failed")
    if models_failed not in (None, 0):
        return f"artefact came from a run with models_failed={models_failed!r}"
    return None


def validate_uhl_serving_bundle(bundle: object, *, require_deployable: bool = True) -> None:
    if not isinstance(bundle, dict):
        raise UhlServingContractError("UHL artefact must be a joblib bundle dictionary")
    if "model" not in bundle:
        raise UhlServingContractError("UHL artefact bundle is missing model")
    if not callable(getattr(bundle["model"], "predict", None)):
        raise UhlServingContractError("UHL artefact model does not provide predict()")
    if int(bundle.get("artifact_contract_version", -1)) != ARTIFACT_CONTRACT_VERSION:
        raise UhlServingContractError(
            f"UHL artefact contract version is not v{ARTIFACT_CONTRACT_VERSION}"
        )
    if bundle.get("input_type") != RAW_INPUT_TYPE:
        raise UhlServingContractError("UHL artefact input_type does not match final serving")
    if list(bundle.get("raw_input_columns") or []) != list(MODEL_INPUT_COLUMNS):
        raise UhlServingContractError("UHL artefact raw_input_columns do not match serving")
    if bundle.get("synthetic_triage_vitals_used") is not True:
        raise UhlServingContractError("UHL artefact must declare synthetic_triage_vitals_used=true")
    if bundle.get("triage_timezone") != TRIAGE_TIMEZONE:
        raise UhlServingContractError(
            f"UHL artefact triage_timezone must be {TRIAGE_TIMEZONE!r}"
        )
    if bundle.get("triage_timestamp_policy") != TRIAGE_TIMESTAMP_POLICY:
        raise UhlServingContractError(
            "UHL artefact triage_timestamp_policy does not match serving"
        )
    age_support = bundle.get("fitted_age_support")
    if not isinstance(age_support, dict):
        raise UhlServingContractError(
            "UHL artefact does not record fitted_age_support"
        )
    try:
        minimum = float(age_support["minimum"])
        maximum = float(age_support["maximum"])
    except Exception as exc:
        raise UhlServingContractError(
            "UHL artefact fitted_age_support is invalid"
        ) from exc
    if (
        not np.isfinite(minimum)
        or not np.isfinite(maximum)
        or minimum < 0
        or maximum > 130
        or minimum > maximum
        or age_support.get("outside_support_policy")
        != "reject_and_route_to_deterministic_review"
    ):
        raise UhlServingContractError(
            "UHL artefact fitted_age_support policy is invalid"
        )
    seen_complaints = [
        re.sub(r"\s+", " ", str(value or "").strip().upper())
        for value in (bundle.get("seen_presenting_complaints") or [])
    ]
    if not seen_complaints:
        raise UhlServingContractError(
            "UHL artefact does not record seen_presenting_complaints"
        )
    if set(seen_complaints).intersection(OUT_OF_MODEL_SCOPE_COMPLAINTS):
        raise UhlServingContractError(
            "UHL artefact includes an out-of-model-scope complaint category"
        )
    if require_deployable:
        reason = deployability_failure_reason(bundle)
        if reason is not None:
            raise UhlServingContractError(f"UHL artefact is not deployable: {reason}")
