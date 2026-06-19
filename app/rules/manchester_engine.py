"""
Clinical Safety Rules Engine.

DESIGN INTENT
=============
This engine has two distinct layers with different statuses:

LAYER 1 — VITAL-SIGN SAFETY FLAGS (deterministic, always active)
-----------------------------------------------------------------
Standard physiological danger thresholds, consistent with NEWS2
and NICE deterioration guidance:
  - SpO2 < 90%        → CRITICAL_HYPOXIA
  - RR < 8 or > 29    → CRITICAL_RESPIRATORY_RATE
  - HR < 40 or > 130  → CRITICAL_HEART_RATE
  - SBP < 90          → CRITICAL_HYPOTENSION
  - Temp < 35°C / > 41°C (Fahrenheit: < 95 / ≥ 105.8) → CRITICAL_TEMPERATURE

These do NOT assign a Manchester category, colour, priority number,
or max-wait time. They produce CRITICAL_PHYSIOLOGY_FLAGGED with
reason codes and require human clinical review.

Amber (less severe) vital derangements produce PHYSIOLOGY_CONCERN_FLAGGED.

LAYER 2 — MTS PATHWAY ENGINE (gated, disabled by default)
---------------------------------------------------------
The Manchester Triage System complaint pathway logic (chest pain,
dyspnoea, stroke, trauma, fever, etc.) is implemented but DISABLED
by default. It will only assign MTS categories (Red/Orange/Yellow/
Green/Blue) when an approved clinical ruleset is registered via
`register_approved_ruleset()`.

Until then, all pathway evaluation produces:
  classification_status = AWAITING_APPROVED_CLINICAL_RULESET
  category              = None
  priority              = None

This design means:
  - A patient with SpO2 85% ALWAYS gets CRITICAL_PHYSIOLOGY_FLAGGED
  - No patient EVER gets "Immediate / Red / 0 min" from keyword rules
    unless a clinician-approved ruleset has been registered
  - requires_clinician_review is ALWAYS True on every output

REGISTERING AN APPROVED RULESET
================================
When your clinical governance process has approved a ruleset:

    from app.rules.manchester_engine import register_approved_ruleset
    register_approved_ruleset(
        ruleset_id="uhl-mts-v1",
        approved_by="Dr. Example",
        approved_date="2025-01-01",
        source="Manchester Triage Group 3rd Ed. + UHL adaptation",
    )

The pathway engine will then activate and assign MTS categories.
The ruleset record is included in every audit output.

TEMPERATURE UNITS
=================
The engine converts `TriageTimeInput.temperature` using `temperature_unit`.
Kaggle KTAS BT is Celsius; MIMIC temperatures are Fahrenheit.
Thresholds are evaluated internally in Celsius.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from app.schemas.internal import TriageTimeInput
from app.schemas.workflow import ManchesterDecision
from app.rules.vitals import temperature_c as _temperature_c


# ── Approved ruleset registry ─────────────────────────────────────────────────
# Empty by default. Populated by register_approved_ruleset() when governance
# has approved a clinical ruleset.

_APPROVED_RULESET: Optional[dict] = None


def register_approved_ruleset(
    ruleset_id: str,
    approved_by: str,
    approved_date: str,
    source: str,
    acknowledge_heuristic_pathways: bool = False,
) -> None:
    """
    Register a clinician-approved ruleset to enable MTS pathway classification.

    IMPORTANT — READ BEFORE CALLING
    ================================
    Calling this function activates the complaint pathway engine, which uses
    keyword-based heuristic rules (e.g. "chest pain" → Very Urgent if pain ≥ 7).

    These pathways are a research prototype. They are NOT:
      - Licensed Manchester Triage Group discriminator trees
      - Validated against real nurse-assigned MTS labels
      - Approved for autonomous clinical use

    You must set acknowledge_heuristic_pathways=True to confirm that you
    understand the pathway logic is heuristic and unvalidated, and that
    all outputs will still require clinician confirmation.

    This function must only be called after formal clinical governance sign-off
    on the specific pathway rules in app/rules/manchester_engine.py.
    The registry is in-memory; call this at application startup.

    Parameters
    ----------
    ruleset_id : str       Unique identifier for this approval record
    approved_by : str      Name/role of approving clinician or committee
    approved_date : str    ISO date of approval (YYYY-MM-DD)
    source : str           Source document or governance record reference
    acknowledge_heuristic_pathways : bool
                           Must be True to confirm pathway limitations understood
    """
    if not acknowledge_heuristic_pathways:
        raise ValueError(
            "register_approved_ruleset() requires acknowledge_heuristic_pathways=True. "
            "Read the docstring — the pathway logic is heuristic and unvalidated. "
            "Set this flag only after reviewing the pathway rules in manchester_engine.py "
            "and obtaining formal clinical governance sign-off."
        )

    global _APPROVED_RULESET
    _APPROVED_RULESET = {
        "ruleset_id": ruleset_id,
        "approved_by": approved_by,
        "approved_date": approved_date,
        "source": source,
        "pathway_type": "HEURISTIC_KEYWORD_RULES_UNVALIDATED",
        "validation_status": "REQUIRES_CLINICAL_VALIDATION_BEFORE_CLINICAL_USE",
    }


def get_approved_ruleset() -> Optional[dict]:
    """Return the currently registered approved ruleset, or None."""
    return _APPROVED_RULESET


def clear_approved_ruleset() -> None:
    """
    Remove any registered ruleset, returning the engine to its fully-gated
    state (no MTS category assigned to any case).

    Used to disable provisional mode and for test isolation, so a test that
    registers a ruleset cannot leak that state into a later test.
    """
    global _APPROVED_RULESET
    _APPROVED_RULESET = None


def mts_pathway_enabled() -> bool:
    """True only when an approved ruleset has been registered."""
    return _APPROVED_RULESET is not None


# ── MTS category constants ────────────────────────────────────────────────────
CAT_IMMEDIATE:   Tuple = ("Immediate",   1,   0, "Red")
CAT_VERY_URGENT: Tuple = ("Very Urgent", 2,  10, "Orange")
CAT_URGENT:      Tuple = ("Urgent",      3,  60, "Yellow")
CAT_STANDARD:    Tuple = ("Standard",    4, 120, "Green")
CAT_NON_URGENT:  Tuple = ("Non-Urgent",  5, 240, "Blue")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _ruleset_is_provisional(ruleset: Optional[dict]) -> bool:
    """
    True when the active ruleset is a provisional/unvalidated research ruleset
    rather than a genuinely clinician-approved one.

    Keyed off validation_status, which register_approved_ruleset() always
    stamps. Any ruleset still carrying the REQUIRES_CLINICAL_VALIDATION marker
    is provisional. This is what makes default-on safe: the provisional nature
    is derived from the ruleset itself and travels onto every decision's
    classification_status, so it cannot be lost between here and the UI.
    """
    if not ruleset:
        return False
    return ruleset.get("validation_status") != "CLINICALLY_APPROVED"


def _category_status(ruleset: Optional[dict]) -> str:
    """Status string for a category-bearing decision, provisional-aware."""
    if _ruleset_is_provisional(ruleset):
        return "PROVISIONAL_MTS_CATEGORY_PENDING_CLINICIAN_REVIEW"
    return "MTS_CATEGORY_ASSIGNED_PENDING_CLINICIAN_REVIEW"


def _make_decision(
    cat: Tuple,
    reason_codes: List[str],
    ruleset: Optional[dict] = None,
) -> ManchesterDecision:
    name, priority, wait, colour = cat
    extra = ["PROVISIONAL_UNVALIDATED_RULESET"] if _ruleset_is_provisional(ruleset) else []
    return ManchesterDecision(
        classification_status=_category_status(ruleset),
        category=f"{name} ({colour})",
        priority=priority,
        max_wait_minutes=wait,
        reason_codes=reason_codes + extra,
        requires_clinician_review=True,
        ruleset_id=ruleset["ruleset_id"] if ruleset else None,
    )


def _safety_alert(
    reason_codes: List[str],
    severity: str = "CRITICAL",
) -> ManchesterDecision:
    """
    Produce a safety alert WITHOUT assigning an MTS category.
    Used when physiology is dangerous but no approved ruleset is active.
    """
    status = (
        "CRITICAL_PHYSIOLOGY_FLAGGED"
        if severity == "CRITICAL"
        else "PHYSIOLOGY_CONCERN_FLAGGED"
    )
    return ManchesterDecision(
        classification_status=status,
        category=None,
        priority=None,
        max_wait_minutes=None,
        reason_codes=reason_codes,
        requires_clinician_review=True,
        ruleset_id=None,
    )


def _awaiting_ruleset(reason_codes: List[str]) -> ManchesterDecision:
    """Pathway matched but no approved ruleset registered."""
    return ManchesterDecision(
        classification_status="AWAITING_APPROVED_CLINICAL_RULESET",
        category=None,
        priority=None,
        max_wait_minutes=None,
        reason_codes=reason_codes + ["MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET"],
        requires_clinician_review=True,
        ruleset_id=None,
    )


def _no_decision(reason_codes: List[str]) -> ManchesterDecision:
    """Insufficient data to classify."""
    return ManchesterDecision(
        classification_status="REQUIRES_CLINICIAN_REVIEW",
        category=None,
        priority=None,
        max_wait_minutes=None,
        reason_codes=reason_codes,
        requires_clinician_review=True,
        ruleset_id=None,
    )


# ── Vital sign parsing ────────────────────────────────────────────────────────

def _parse_pain(t: TriageTimeInput) -> Optional[int]:
    if t.pain is None:
        return None
    try:
        val = int(float(str(t.pain).strip()))
        return val if 0 <= val <= 10 else None
    except (ValueError, TypeError):
        return None



def _cc(t: TriageTimeInput) -> str:
    return (t.chiefcomplaint or "").lower()


def _cc_contains(t: TriageTimeInput, *terms: str) -> bool:
    cc = _cc(t)
    return any(term in cc for term in terms)


# ── LAYER 1: Vital-sign safety detection (always active) ─────────────────────
# Thresholds consistent with NEWS2 high-risk bands and NICE deterioration guidance.
# These never assign MTS colours — they produce named safety flags only.

def _critical_vital_flags(t: TriageTimeInput) -> List[str]:
    """Returns non-empty list when any critical physiology threshold is breached."""
    flags: List[str] = []
    if t.o2sat is not None and t.o2sat < 90:
        flags.append("CRITICAL_HYPOXIA_SPO2_BELOW_90")
    if t.resprate is not None and t.resprate > 29:
        flags.append("CRITICAL_RESPIRATORY_RATE_ABOVE_29")
    if t.resprate is not None and t.resprate < 8:
        flags.append("CRITICAL_RESPIRATORY_RATE_BELOW_8")
    if t.heartrate is not None and t.heartrate > 130:
        flags.append("CRITICAL_HEART_RATE_ABOVE_130")
    if t.heartrate is not None and t.heartrate < 40:
        flags.append("CRITICAL_HEART_RATE_BELOW_40")
    if t.sbp is not None and t.sbp < 90:
        flags.append("CRITICAL_HYPOTENSION_SBP_BELOW_90")
    if t.sbp is not None and t.sbp > 220:
        flags.append("CRITICAL_HYPERTENSION_SBP_ABOVE_220")
    temp_c = _temperature_c(t)
    if temp_c is not None and temp_c >= 41.0:
        flags.append("CRITICAL_HYPERPYREXIA_TEMP_ABOVE_41C")
    if temp_c is not None and temp_c < 35.0:
        flags.append("CRITICAL_HYPOTHERMIA_TEMP_BELOW_35C")
    return flags


def _concern_vital_flags(t: TriageTimeInput) -> List[str]:
    """Returns non-empty list for concerning (amber) vital derangements."""
    flags: List[str] = []
    if t.o2sat is not None and 90 <= t.o2sat < 95:
        flags.append("CONCERN_SPO2_90_TO_94")
    if t.resprate is not None and 25 <= t.resprate <= 29:
        flags.append("CONCERN_RESPIRATORY_RATE_25_TO_29")
    if t.heartrate is not None and 100 < t.heartrate <= 130:
        flags.append("CONCERN_HEART_RATE_101_TO_130")
    if t.sbp is not None and 90 <= t.sbp < 100:
        flags.append("CONCERN_SBP_90_TO_99")
    temp_c = _temperature_c(t)
    if temp_c is not None and 39.5 <= temp_c < 41.0:
        flags.append("CONCERN_HIGH_FEVER_39_5_TO_41C")
    return flags


# ── LAYER 2: MTS complaint pathway functions (gated) ─────────────────────────
# These functions are only called when mts_pathway_enabled() is True.
# They assign MTS categories per the registered approved ruleset.

def _pathway_chest_pain(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    cardiac_kw = [
        "cardiac", "crushing", "pressure", "tightness", "tight", "squeezing",
        "radiation", "radiating", "jaw", "left arm", "diaphoresis", "sweating",
        "acs", "stemi", "nstemi", "angina", "nausea",
    ]
    has_cardiac = any(kw in _cc(t) for kw in cardiac_kw)
    pain = _parse_pain(t)
    if has_cardiac:
        return _make_decision(CAT_VERY_URGENT, ["CHEST_PAIN_CARDIAC_FEATURE"], ruleset)
    if pain is not None and pain >= 7:
        return _make_decision(CAT_VERY_URGENT, [f"CHEST_PAIN_SEVERE_PAIN_{pain}"], ruleset)
    if pain is not None and 4 <= pain < 7:
        return _make_decision(CAT_URGENT, [f"CHEST_PAIN_MODERATE_PAIN_{pain}"], ruleset)
    return _make_decision(CAT_URGENT, ["CHEST_PAIN_MILD_OR_UNKNOWN_SEVERITY"], ruleset)


def _pathway_dyspnoea(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    severe = ["severe", "acute", "sudden", "worst", "unable to speak",
              "silent chest", "can't breathe", "cannot breathe"]
    if _cc_contains(t, *severe):
        return _make_decision(CAT_VERY_URGENT, ["DYSPNOEA_SEVERE_FEATURE"], ruleset)
    return _make_decision(CAT_URGENT, ["DYSPNOEA_PATHWAY_MODERATE"], ruleset)


def _pathway_stroke(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    acute = ["sudden", "acute", "onset", "focal", "slurred", "weakness",
             "face droop", "arm weak", "speech", "fast", "facial", "numb"]
    if _cc_contains(t, *acute):
        return _make_decision(CAT_VERY_URGENT, ["STROKE_ACUTE_NEUROLOGICAL_FEATURE"], ruleset)
    return _make_decision(CAT_URGENT, ["STROKE_TIA_PATHWAY_MODERATE"], ruleset)


def _pathway_trauma(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    major = ["mva", "motor vehicle", "car accident", "motorcycle",
             "fall from height", "fall from ladder", "fall from roof",
             "gunshot", "stab", "penetrating", "crush", "ejected", "intubated"]
    if _cc_contains(t, *major):
        return _make_decision(CAT_VERY_URGENT, ["TRAUMA_MAJOR_MECHANISM"], ruleset)
    pain = _parse_pain(t)
    if pain is not None and pain >= 7:
        return _make_decision(CAT_URGENT, [f"TRAUMA_SEVERE_PAIN_{pain}"], ruleset)
    return _make_decision(CAT_URGENT, ["TRAUMA_PATHWAY_MODERATE"], ruleset)


def _pathway_fever(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    sepsis = ["sepsis", "septic", "meningism", "stiff neck", "rash", "rigors",
              "confusion", "altered mental", "lethargic", "immunocompromised",
              "neutropenic", "transplant", "hiv", "altered", "unresponsive"]
    if _cc_contains(t, *sepsis):
        return _make_decision(CAT_VERY_URGENT, ["FEVER_SEPSIS_INDICATOR"], ruleset)
    temp_c = _temperature_c(t)
    if temp_c is not None and temp_c >= 38.5:
        return _make_decision(CAT_URGENT, ["FEVER_ELEVATED_TEMP"], ruleset)
    return _make_decision(CAT_STANDARD, ["FEVER_PATHWAY_MILD"], ruleset)


def _pathway_abdominal(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    vascular = ["ruptured", "aortic", "vascular", "pulsating", "aneurysm",
                "peritoneal", "board-like", "rigid", "guarding", "ectopic"]
    if _cc_contains(t, *vascular):
        return _make_decision(CAT_VERY_URGENT, ["ABDO_VASCULAR_OR_PERITONEAL"], ruleset)
    pain = _parse_pain(t)
    if pain is not None and pain >= 7:
        return _make_decision(CAT_URGENT, [f"ABDO_SEVERE_PAIN_{pain}"], ruleset)
    if pain is not None and 4 <= pain < 7:
        return _make_decision(CAT_URGENT, [f"ABDO_MODERATE_PAIN_{pain}"], ruleset)
    return _make_decision(CAT_STANDARD, ["ABDO_MILD_OR_UNKNOWN"], ruleset)


def _pathway_altered_consciousness(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    return _make_decision(CAT_VERY_URGENT, ["ALTERED_CONSCIOUSNESS_PATHWAY"], ruleset)


def _pathway_overdose(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    return _make_decision(CAT_VERY_URGENT, ["OVERDOSE_POISONING_PATHWAY"], ruleset)


def _pathway_anaphylaxis(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    return _make_decision(CAT_IMMEDIATE, ["ANAPHYLAXIS_PATHWAY"], ruleset)


def _pathway_cardiac_arrest(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    return _make_decision(CAT_IMMEDIATE, ["CARDIAC_ARREST_PROXY"], ruleset)


def _pathway_pain_generic(t: TriageTimeInput, ruleset: dict) -> ManchesterDecision:
    pain = _parse_pain(t)
    if pain is not None and pain >= 7:
        return _make_decision(CAT_URGENT, [f"PAIN_SEVERE_{pain}"], ruleset)
    if pain is not None and 4 <= pain < 7:
        return _make_decision(CAT_URGENT, [f"PAIN_MODERATE_{pain}"], ruleset)
    if pain is not None and pain < 4:
        return _make_decision(CAT_STANDARD, [f"PAIN_MILD_{pain}"], ruleset)
    return _no_decision(["PAIN_PATHWAY_MISSING_SCORE"])


# Keyword → pathway function mapping (ordered, first match wins)
_PATHWAYS: List[Tuple[List[str], Callable]] = [
    (["cardiac arrest", "pulseless", "not breathing", "no pulse", " arrest"],
     _pathway_cardiac_arrest),
    (["anaphylaxis", "anaphylactic", "severe allergic", "throat closing",
      "tongue swelling"], _pathway_anaphylaxis),
    (["chest pain", "chest tightness", "chest pressure", "chest discomfort",
      "substernal"], _pathway_chest_pain),
    (["shortness of breath", "sob", "dyspnoea", "dyspnea",
      "difficulty breathing", "breathless", "respiratory distress"],
     _pathway_dyspnoea),
    (["stroke", "tia", "facial droop", "arm weakness", "slurred speech",
      "sudden weakness", "sudden numbness", "worst headache"], _pathway_stroke),
    (["trauma", "injury", "mva", "fall", "assault", "laceration", "fracture",
      "motor vehicle", "accident", "collision", "hit by", "intubated"],
     _pathway_trauma),
    (["fever", "high temp", "pyrexia", "hot", "chills", "rigors", "febrile"],
     _pathway_fever),
    (["abdominal pain", "abdo pain", "stomach pain", "belly pain",
      "epigastric", "flank pain", "rlq", "llq", "abdominal", "abd pain"],
     _pathway_abdominal),
    (["altered mental", "confusion", "confused", "disoriented", "unresponsive",
      "unconscious", "syncope", "collapse", "gcs", "lethargic",
      "hallucination", "etoh"], _pathway_altered_consciousness),
    (["overdose", " od ", "od,", "poisoning", "ingestion",
      "swallowed", "toxic", "intoxication"], _pathway_overdose),
    (["pain"], _pathway_pain_generic),
]


def _select_pathway(t: TriageTimeInput) -> Optional[Callable]:
    if not t.chiefcomplaint:
        return None
    cc = t.chiefcomplaint.lower()
    for keywords, fn in _PATHWAYS:
        if any(kw in cc for kw in keywords):
            return fn
    return None


# ── Public entry point ────────────────────────────────────────────────────────

def run_manchester_engine(triage_input: TriageTimeInput) -> ManchesterDecision:
    """
    Clinical safety rules engine.

    ALWAYS produces requires_clinician_review = True.

    Decision flow:
      1. Critical vital-sign detection (always active, never assigns MTS category)
         → CRITICAL_PHYSIOLOGY_FLAGGED with named reason codes
      2. Concerning vital-sign detection (always active)
         → PHYSIOLOGY_CONCERN_FLAGGED
      3. If approved ruleset registered: run MTS complaint pathway
         → MTS_CATEGORY_ASSIGNED_PENDING_CLINICIAN_REVIEW
      4. If no approved ruleset AND a pathway keyword matched the complaint:
         pathway detected but blocked
         → AWAITING_APPROVED_CLINICAL_RULESET (reason codes include
           PATHWAY_MATCHED_{NAME} + MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET)
      5. If no approved ruleset AND no pathway keyword matched the complaint
         (complaint present but unrecognised): same AWAITING status as (4),
         not REQUIRES_CLINICIAN_REVIEW -- this is a genuinely different
         code path from (6) below, even though both ultimately have no
         category assigned
         → AWAITING_APPROVED_CLINICAL_RULESET (reason codes:
           UNRECOGNISED_COMPLAINT + MTS_PATHWAY_DISABLED_NO_APPROVED_RULESET)
      6. Missing chief complaint entirely (None, not just unrecognised)
         → REQUIRES_CLINICIAN_REVIEW

    Verified directly against this function's real output for every branch
    above; see tests/test_triage_indicator_matrix.py, which derives its
    expected values the same way rather than from this docstring.
    """
    # Step 0 — dataset gate for the MANCHESTER CATEGORY only.
    # The Manchester-style category (Red/Orange/Yellow/Green/Blue) applies ONLY
    # to MIMIC-IV-ED Demo cases; KTAS is a different triage system and must
    # never show an MTS category. HOWEVER, the deterministic critical/concern
    # vital-sign SAFETY detection still runs for KTAS, because dangerous
    # physiology must always be surfaced regardless of dataset -- removing that
    # would be a safety regression. So for KTAS we run the same vital checks but
    # emit a KTAS-specific status and never an MTS category.
    is_ktas = triage_input.source_dataset == "Kaggle-KTAS"
    if is_ktas:
        critical_flags = _critical_vital_flags(triage_input)
        if critical_flags:
            return ManchesterDecision(
                classification_status="CRITICAL_PHYSIOLOGY_FLAGGED",
                category=None,
                priority=None,
                max_wait_minutes=None,
                reason_codes=["CRITICAL_PHYSIOLOGY_DETECTED", "MTS_DISABLED_FOR_KTAS"] + critical_flags,
                requires_clinician_review=True,
                ruleset_id=None,
            )
        concern_flags = _concern_vital_flags(triage_input)
        if concern_flags:
            return ManchesterDecision(
                classification_status="PHYSIOLOGY_CONCERN_FLAGGED",
                category=None,
                priority=None,
                max_wait_minutes=None,
                reason_codes=["CONCERNING_PHYSIOLOGY_DETECTED", "MTS_DISABLED_FOR_KTAS"] + concern_flags,
                requires_clinician_review=True,
                ruleset_id=None,
            )
        return ManchesterDecision(
            classification_status="MTS_NOT_APPLIED_TO_KTAS_DATASET",
            category=None,
            priority=None,
            max_wait_minutes=None,
            reason_codes=["MTS_DISABLED_FOR_KTAS"],
            requires_clinician_review=True,
            ruleset_id=None,
        )

    ruleset = get_approved_ruleset()
    critical_flags = _critical_vital_flags(triage_input)
    if critical_flags:
        if ruleset:
            # With approved ruleset: critical vitals map to Immediate
            return _make_decision(
                CAT_IMMEDIATE,
                ["CRITICAL_PHYSIOLOGY_DETECTED"] + critical_flags,
                ruleset,
            )
        else:
            # Without approved ruleset: alert only, no MTS category
            return _safety_alert(
                ["CRITICAL_PHYSIOLOGY_DETECTED"] + critical_flags,
                severity="CRITICAL",
            )

    # Step 2 — concerning physiology (stored for possible upgrade in step 3)
    concern_flags = _concern_vital_flags(triage_input)

    # Step 3/4 — complaint pathway
    if not triage_input.chiefcomplaint:
        if concern_flags:
            return _safety_alert(
                ["MISSING_CHIEF_COMPLAINT"] + concern_flags,
                severity="CONCERN",
            )
        return _no_decision(["MISSING_CHIEF_COMPLAINT"])

    pathway_fn = _select_pathway(triage_input)

    if pathway_fn is None:
        # Unrecognised complaint — use pain score fallback
        pain = _parse_pain(triage_input)
        if not ruleset:
            codes = [f"UNRECOGNISED_COMPLAINT"]
            if concern_flags:
                codes += concern_flags
            return _awaiting_ruleset(codes)
        if pain is not None and pain >= 7:
            return _make_decision(
                CAT_URGENT, [f"UNRECOGNISED_COMPLAINT_SEVERE_PAIN_{pain}"], ruleset
            )
        if pain is not None and 4 <= pain < 7:
            return _make_decision(
                CAT_STANDARD, [f"UNRECOGNISED_COMPLAINT_MODERATE_PAIN_{pain}"], ruleset
            )
        if pain is not None and pain < 4:
            return _make_decision(
                CAT_NON_URGENT, [f"UNRECOGNISED_COMPLAINT_MILD_PAIN_{pain}"], ruleset
            )
        return _make_decision(
            CAT_STANDARD, ["UNRECOGNISED_COMPLAINT_NO_PAIN_SCORE"], ruleset
        )

    if not ruleset:
        # Pathway matched but no approved ruleset — produce concern/awaiting status
        pathway_name = pathway_fn.__name__.replace("_pathway_", "").upper()
        codes = [f"PATHWAY_MATCHED_{pathway_name}"]
        if concern_flags:
            codes += concern_flags
            return _safety_alert(codes, severity="CONCERN")
        return _awaiting_ruleset(codes)

    # Pathway with approved ruleset — run it
    result = pathway_fn(triage_input, ruleset)

    # Upgrade to Very Urgent if concern vitals and result is Urgent or lower
    if concern_flags and result.priority is not None and result.priority >= 3:
        name, priority, wait, colour = CAT_VERY_URGENT
        extra = ["PROVISIONAL_UNVALIDATED_RULESET"] if _ruleset_is_provisional(ruleset) else []
        return ManchesterDecision(
            classification_status=_category_status(ruleset),
            category=f"{name} ({colour})",
            priority=priority,
            max_wait_minutes=wait,
            reason_codes=list(result.reason_codes) + ["CONCERN_VITAL_UPGRADE"] + concern_flags + extra,
            requires_clinician_review=True,
            ruleset_id=ruleset["ruleset_id"],
        )

    return result
