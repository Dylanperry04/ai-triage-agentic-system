"""
Safety Review Agent.

Checks for:
  1. Data quality issues (missing vitals, contradictions)
  2. Leakage guard compliance
  3. High-risk complaint patterns that might be under-triaged
  4. Conditions requiring mandatory escalation

Does NOT assign any clinical category.
Does NOT call any LLM.
All checks are deterministic.
"""
from app.schemas.internal import TriageTimeInput
from app.schemas.workflow import SafetyReviewResult
from app.rules.leakage_guard import validate_triage_time_input
from app.rules.vitals import temperature_c as _temperature_c


# Vital sign fields whose absence is a safety concern
CRITICAL_VITAL_FIELDS = ["temperature", "heartrate", "resprate", "o2sat", "sbp"]

# Chief complaint keywords that are inherently high-risk and warrant explicit flagging
# even if vitals appear normal. These must be escalated to the clinician's attention.
HIGH_RISK_COMPLAINT_PATTERNS = [
    ("cardiac arrest", "CARDIAC_ARREST_COMPLAINT"),
    ("chest pain", "CHEST_PAIN_HIGH_RISK"),
    ("shortness of breath", "DYSPNOEA_HIGH_RISK"),
    ("dyspnea", "DYSPNOEA_HIGH_RISK"),
    ("stroke", "STROKE_COMPLAINT_HIGH_RISK"),
    ("anaphylaxis", "ANAPHYLAXIS_COMPLAINT_HIGH_RISK"),
    ("overdose", "OVERDOSE_COMPLAINT_HIGH_RISK"),
    ("suicide", "SELF_HARM_RISK"),
    ("suicidal", "SELF_HARM_RISK"),
    ("self harm", "SELF_HARM_RISK"),
    ("altered mental", "ALTERED_CONSCIOUSNESS_HIGH_RISK"),
    ("mental change", "ALTERED_CONSCIOUSNESS_HIGH_RISK"),
    ("unconscious", "ALTERED_CONSCIOUSNESS_HIGH_RISK"),
    ("unresponsive", "ALTERED_CONSCIOUSNESS_HIGH_RISK"),
    ("sepsis", "SEPSIS_HIGH_RISK"),
    ("intubated", "INTUBATED_ARRIVAL_HIGH_RISK"),
]


def run_safety_review(triage_input: TriageTimeInput) -> SafetyReviewResult:
    """
    Runs all safety checks on the triage-time input.

    Returns SafetyReviewResult with:
      - data_quality_flags: issues that may prevent safe classification
      - leakage_guard_passed: whether retrospective data is absent (should always be True)
      - is_safe_to_present: False if critical data is missing
      - critical_missing_vitals: vital fields that are absent
      - notes: human-readable explanations

    is_safe_to_present=False does not block the workflow — it flags that the
    clinician needs to be aware of limitations before acting on any output.
    """
    data_quality_flags: list[str] = []
    critical_missing: list[str] = []
    notes: list[str] = []

    # ── Leakage guard ─────────────────────────────────────────────────────────
    try:
        leakage_ok = validate_triage_time_input(triage_input)
    except ValueError as exc:
        leakage_ok = False
        data_quality_flags.append(f"LEAKAGE_DETECTED: {exc}")

    # ── Missing vital signs ───────────────────────────────────────────────────
    payload = triage_input.model_dump()
    for field in CRITICAL_VITAL_FIELDS:
        if payload.get(field) is None:
            critical_missing.append(field)
            data_quality_flags.append(f"MISSING_CRITICAL_VITAL:{field}")

    # ── SpO2 safety: very low with normal or slow HR is suspicious ────────────
    if triage_input.o2sat is not None and triage_input.o2sat < 85:
        data_quality_flags.append("CRITICAL_HYPOXIA_SPO2_BELOW_85")

    # ── Contradictory vital signs ─────────────────────────────────────────────
    # Very high pain + SpO2 100 + normal everything may indicate data entry error
    # We flag it but do not block — the clinician resolves.
    if (
        triage_input.pain is not None
        and triage_input.o2sat is not None
        and triage_input.heartrate is not None
    ):
        try:
            pain_val = int(float(str(triage_input.pain)))
            if (
                pain_val >= 10
                and triage_input.o2sat == 100
                and 60 <= triage_input.heartrate <= 80
            ):
                data_quality_flags.append("POSSIBLE_CONTRADICTORY_VITALS_REVIEW_REQUIRED")
                notes.append(
                    "Maximum pain score with completely normal SpO2 and resting HR — "
                    "please verify data entry."
                )
        except (ValueError, TypeError):
            pass

    # ── Missing chief complaint ───────────────────────────────────────────────
    if not triage_input.chiefcomplaint or str(triage_input.chiefcomplaint).strip() == "":
        data_quality_flags.append("MISSING_CHIEF_COMPLAINT")

    # ── High-risk complaint patterns ──────────────────────────────────────────
    high_risk_complaint_detected = False
    if triage_input.chiefcomplaint:
        cc_lower = triage_input.chiefcomplaint.lower()
        for pattern, code in HIGH_RISK_COMPLAINT_PATTERNS:
            if pattern in cc_lower:
                data_quality_flags.append(f"HIGH_RISK_COMPLAINT_PATTERN:{code}")
                high_risk_complaint_detected = True

    # ── Detect critically abnormal vital signs (present but dangerous) ───────
    # These are distinct from MISSING vitals — the value is present but indicates
    # critical physiological derangement. Both conditions make is_safe_to_present False.
    has_critical_physiology = False
    if triage_input.o2sat is not None and triage_input.o2sat < 90:
        data_quality_flags.append("CRITICAL_PHYSIOLOGY:SPO2_BELOW_90")
        has_critical_physiology = True
    if triage_input.resprate is not None and (triage_input.resprate > 29 or triage_input.resprate < 8):
        data_quality_flags.append("CRITICAL_PHYSIOLOGY:RESPIRATORY_RATE_EXTREME")
        has_critical_physiology = True
    if triage_input.heartrate is not None and (triage_input.heartrate > 130 or triage_input.heartrate < 40):
        data_quality_flags.append("CRITICAL_PHYSIOLOGY:HEART_RATE_EXTREME")
        has_critical_physiology = True
    if triage_input.sbp is not None and triage_input.sbp < 90:
        data_quality_flags.append("CRITICAL_PHYSIOLOGY:HYPOTENSION_SBP_BELOW_90")
        has_critical_physiology = True
    temp_c = _temperature_c(triage_input)
    if temp_c is not None and (temp_c >= 41.0 or temp_c < 35.0):
        data_quality_flags.append("CRITICAL_PHYSIOLOGY:TEMPERATURE_EXTREME_CELSIUS_THRESHOLD")
        has_critical_physiology = True

    # ── Determine if safe to present ─────────────────────────────────────────
    # False when: missing critical vitals, critical physiology detected,
    # leakage guard failed, chief complaint absent, OR a high-risk complaint
    # pattern was matched (cardiac arrest, stroke, anaphylaxis, overdose,
    # self-harm, altered consciousness, sepsis, intubated arrival -- see
    # HIGH_RISK_COMPLAINT_PATTERNS above). This last condition was ADDED
    # during a later review pass: confirmed directly that a case with a
    # chief complaint of "cardiac arrest witnessed" but otherwise
    # normal-looking recorded vitals previously returned
    # is_safe_to_present=True, since the high-risk-pattern match was only
    # ever recorded as a string in data_quality_flags with nothing
    # downstream consulting it -- meaning the orchestrator's
    # workflow_action (app/agents/orchestrator.py) would render this as
    # the calmest possible label for one of the most dangerous complaint
    # categories the system recognises. A complaint this severe should
    # never be treated as routine regardless of what the recorded vitals
    # happen to show, since the complaint itself is independent evidence
    # of risk that vitals alone cannot rule out (vitals can look normal
    # moments before or after a genuine cardiac event, and a witnessed
    # arrest is definitionally a critical event regardless of current
    # readings).
    # Note: is_safe_to_present=False does NOT block the workflow — it ensures
    # the clinician sees a clear warning that the output has significant limitations.
    is_safe_to_present = (
        len(critical_missing) == 0
        and not has_critical_physiology
        and not high_risk_complaint_detected
        and leakage_ok
        and "MISSING_CHIEF_COMPLAINT" not in data_quality_flags
    )

    # ── Notes ─────────────────────────────────────────────────────────────────
    notes.append(
        "This safety review covers data quality, leakage, and critical physiology detection. "
        "Temperature is interpreted using temperature_unit. It does not assign or validate clinical triage categories."
    )
    if critical_missing:
        notes.append(
            f"Critical vital signs missing: {', '.join(critical_missing)}. "
            "The rules engine may be unable to classify this case safely."
        )
    if has_critical_physiology:
        notes.append(
            "CRITICAL PHYSIOLOGY DETECTED: one or more vital signs are in a dangerous range. "
            "The clinical safety rules engine has flagged this case. "
            "Immediate clinician assessment is required."
        )
    if high_risk_complaint_detected:
        notes.append(
            "HIGH-RISK COMPLAINT PATTERN DETECTED: the chief complaint matches a "
            "keyword associated with a time-critical condition (e.g. cardiac arrest, "
            "stroke, anaphylaxis, overdose, self-harm, altered consciousness, sepsis, "
            "or intubated arrival). This case is flagged regardless of recorded vital "
            "signs, since a high-risk complaint is independent evidence of risk that "
            "vitals alone do not rule out. Immediate clinician assessment is required."
        )

    return SafetyReviewResult(
        data_quality_flags=data_quality_flags,
        leakage_guard_passed=leakage_ok,
        is_safe_to_present=is_safe_to_present,
        critical_missing_vitals=critical_missing,
        high_risk_complaint_detected=high_risk_complaint_detected,
        notes=notes,
    )
