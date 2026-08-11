"""
Shared vital-sign unit conversion helpers.

This logic was previously duplicated identically in app/rules/manchester_engine.py
and app/agents/safety_review_agent.py. Having two independently maintained copies
of a clinical threshold-conversion function is a safety risk: if one copy is
updated in a future change and the other is not, the two safety-relevant code
paths would silently diverge in how they interpret temperature. This module
exists so there is exactly one implementation, imported by both callers.
"""
from __future__ import annotations

from typing import Optional

from app.schemas.internal import TriageTimeInput


def temperature_c(t: TriageTimeInput) -> Optional[float]:
    """
    Convert TriageTimeInput.temperature to Celsius using temperature_unit.

    Kaggle KTAS BT is Celsius. MIMIC temperatures are Fahrenheit. Any future
    dataset must set temperature_unit correctly at the adapter layer; this
    function only trusts that field, it does not guess based on the dataset.
    """
    if t.temperature is None:
        return None
    unit = (getattr(t, "temperature_unit", "F") or "F").upper()
    if unit.startswith("F"):
        return (float(t.temperature) - 32.0) * 5.0 / 9.0
    return float(t.temperature)
