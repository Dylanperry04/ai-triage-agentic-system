"""
Runs the full triage-indicator test matrix and prints a readable
Indicator | Expected Status | Actual Status | Pass/Fail table, then saves
the same data as JSON. See tests/test_triage_indicator_matrix.py for the
full matrix definition and the methodology used to derive every expected
value from the real engine.

Usage:
  python scripts/run_triage_indicator_matrix.py
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from test_triage_indicator_matrix import ALL_INDICATOR_CASES, _make_input
from app.rules.manchester_engine import run_manchester_engine, clear_approved_ruleset
from app.rules.provisional_mts_ruleset import register_provisional_ruleset


def _run_matrix(mode_label: str):
    """
    Run every indicator case through the engine in the CURRENT ruleset state
    and return (results, all_pass). Caller controls whether a ruleset is
    registered before calling, which is the whole point of the two modes.

    In gated mode the matrix's `expected_status` values (which were derived
    against the gated engine) match exactly. In provisional mode the engine
    assigns categories instead, so this run records the ACTUAL provisional
    output and does NOT pass/fail against the gated expectations -- it is a
    descriptive log of what the default-on app actually produces, not a
    regression check.
    """
    results = []
    all_pass = True
    print(f"\n=== {mode_label} ===")
    print(f"{'Indicator':45s} {'Expected (gated)':32s} {'Actual':40s} {'Match'}")
    print("-" * 130)
    for case in ALL_INDICATOR_CASES:
        triage_input = _make_input(**case.triage_input_overrides)
        decision = run_manchester_engine(triage_input)
        status_ok = decision.classification_status == case.expected_status
        codes_ok = all(c in decision.reason_codes for c in case.expected_reason_codes_subset)
        passed = status_ok and codes_ok
        all_pass = all_pass and passed
        print(
            f"{case.indicator:45s} {case.expected_status:32s} "
            f"{decision.classification_status:40s} {'YES' if passed else 'no'}"
        )
        results.append({
            "indicator": case.indicator,
            "expected_status_gated": case.expected_status,
            "actual_status": decision.classification_status,
            "expected_reason_codes_subset": case.expected_reason_codes_subset,
            "actual_reason_codes": decision.reason_codes,
            "category_assigned": decision.category,
            "matches_gated_expectation": passed,
            "note": case.note,
        })
    return results, all_pass


def run_and_save() -> None:
    out_dir = PROJECT_ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Pass 1: GATED mode (no ruleset registered) ---
    clear_approved_ruleset()
    gated_results, gated_all_pass = _run_matrix(
        "GATED MODE (no approved/provisional ruleset registered)"
    )
    print(f"\nGATED TOTAL: {len(ALL_INDICATOR_CASES)} indicators, "
          f"all match gated expectation: {gated_all_pass}")
    with open(out_dir / "triage_indicator_matrix_log.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "label": "TRIAGE_INDICATOR_MATRIX (GATED MODE) -- constructed "
                         "indicator-level test cases, not real patient data. This "
                         "log reflects the engine with NO ruleset registered (no "
                         "category assigned). The default-on app runs with the "
                         "provisional ruleset; see "
                         "triage_indicator_matrix_provisional_log.json for that.",
                "mode": "GATED_NO_RULESET",
                "methodology": (
                    "Every expected value was derived by directly calling "
                    "run_manchester_engine() with no ruleset registered and "
                    "reading its real output, not assumed or copied from docs."
                ),
                "all_match_gated_expectation": gated_all_pass,
                "results": gated_results,
            },
            f, indent=2,
        )

    # --- Pass 2: PROVISIONAL mode (the app's default-on state) ---
    register_provisional_ruleset()
    prov_results, _ = _run_matrix(
        "PROVISIONAL MODE (provisional research ruleset registered -- the app default)"
    )
    n_categories = sum(1 for r in prov_results if r["category_assigned"])
    print(f"\nPROVISIONAL TOTAL: {len(ALL_INDICATOR_CASES)} indicators, "
          f"{n_categories} now receive a provisional category.")
    with open(out_dir / "triage_indicator_matrix_provisional_log.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "label": "TRIAGE_INDICATOR_MATRIX (PROVISIONAL MODE) -- constructed "
                         "indicator-level test cases, not real patient data. This is "
                         "the engine state the default-on app actually uses: a "
                         "provisional, unvalidated research ruleset is registered, so "
                         "cases receive PROVISIONAL Manchester-style categories. These "
                         "are NOT the official MTS and NOT clinically approved; every "
                         "one requires clinician confirmation.",
                "mode": "PROVISIONAL_RULESET_ACTIVE",
                "methodology": (
                    "Each row is the ACTUAL output of run_manchester_engine() with "
                    "the provisional ruleset registered. This is a descriptive log "
                    "of real engine output, not a pass/fail regression check against "
                    "the gated expectations (which intentionally differ)."
                ),
                "results": prov_results,
            },
            f, indent=2,
        )
    clear_approved_ruleset()
    print(f"\nLogs saved to: {out_dir}/triage_indicator_matrix_log.json (gated) "
          f"and triage_indicator_matrix_provisional_log.json (provisional)")


if __name__ == "__main__":
    run_and_save()
