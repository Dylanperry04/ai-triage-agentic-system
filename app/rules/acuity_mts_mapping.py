"""
Central acuity -> MTS-display mapping.

THE PROJECT RULE (Dylan's decision):
  MIMIC-IV-ED triage `acuity` level (1-5) maps to an MTS-style DISPLAY level
  for this project, as follows:

      acuity 1 -> Immediate (Red),    priority 1, max wait 0   min
      acuity 2 -> Very Urgent (Orange),priority 2, max wait 10  min
      acuity 3 -> Urgent (Yellow),    priority 3, max wait 60  min
      acuity 4 -> Standard (Green),   priority 4, max wait 120 min
      acuity 5 -> Non-Urgent (Blue),  priority 5, max wait 240 min

WHAT THIS MAPPING IS, AND IS NOT
================================
This is a DISPLAY/PRESENTATION convention chosen for this research project so
that a predicted MIMIC acuity can be shown using the familiar five-level
colour/priority scheme. It is the single source of truth for that mapping --
every part of the app (ML agent, UI, follow-up comparison, API, tests, docs)
must import it from here rather than re-hard-coding the table.

It is NOT a claim that MIMIC `acuity` and the official Manchester Triage System
are the same clinical instrument. They are not:
  - MIMIC `acuity` is the Emergency Severity Index (ESI), the US 5-level triage
    scale assigned by the (US) hospital's nurses. It is what the data actually
    contains.
  - The Manchester Triage System (MTS) is a different instrument with its own
    presentation flowcharts and discriminators, used in the UK/Ireland (incl.
    UHL). MIMIC contains NO Manchester labels.

So the colour/priority shown for a MIMIC case is an ESI-acuity value rendered
in an MTS-style display scheme. It must always be labelled as such (e.g.
"acuity mapped to MTS-style display levels (project convention)") and never
presented as an official Manchester Triage System classification, nor as
clinically approved. Clinician review is required on every output.

`mapping_rule_version` is stamped onto every mapped result so any record can be
traced back to this exact table.
"""
from __future__ import annotations

import re as _re

from typing import Optional, Dict, Any

MAPPING_RULE_VERSION = "acuity_to_mts_display_v1"

# acuity level (int) -> display fields. Single source of truth.
MIMIC_ACUITY_TO_MTS: Dict[int, Dict[str, Any]] = {
    1: {"category": "Immediate (Red)",     "priority": 1, "max_wait_minutes": 0,   "colour": "red"},
    2: {"category": "Very Urgent (Orange)", "priority": 2, "max_wait_minutes": 10,  "colour": "orange"},
    3: {"category": "Urgent (Yellow)",     "priority": 3, "max_wait_minutes": 60,  "colour": "yellow"},
    4: {"category": "Standard (Green)",    "priority": 4, "max_wait_minutes": 120, "colour": "green"},
    5: {"category": "Non-Urgent (Blue)",   "priority": 5, "max_wait_minutes": 240, "colour": "blue"},
}

# Hex colours for the UI badge, kept here so the UI does not re-define them.
MTS_DISPLAY_HEX: Dict[str, Dict[str, str]] = {
    "red":    {"bg": "#c0392b", "fg": "#ffffff"},
    "orange": {"bg": "#e67e22", "fg": "#ffffff"},
    "yellow": {"bg": "#f1c40f", "fg": "#1a1a1a"},
    "green":  {"bg": "#27ae60", "fg": "#ffffff"},
    "blue":   {"bg": "#2980b9", "fg": "#ffffff"},
}

# Short, reusable provenance line. Display surfaces should show this (or
# equivalent) next to any mapped category.
MAPPING_PROVENANCE_NOTE = (
    "Predicted MIMIC acuity (ESI) mapped to MTS-style display levels using a "
    "project display convention. NOT the official Manchester Triage System and "
    "NOT clinically approved. Clinician review required."
)


def map_acuity_to_mts(acuity: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    Map a MIMIC acuity level (1-5) to its MTS-style display fields.

    Returns a dict with category/priority/max_wait_minutes/colour plus the
    mapping_rule_version and the provenance note, or None if `acuity` is None
    or not one of 1-5 (so callers can handle "no mapping" explicitly rather
    than guessing).
    """
    if acuity is None:
        return None
    try:
        key = int(acuity)
    except (TypeError, ValueError):
        return None
    base = MIMIC_ACUITY_TO_MTS.get(key)
    if base is None:
        return None
    result = dict(base)
    result["mapping_rule_version"] = MAPPING_RULE_VERSION
    result["provenance_note"] = MAPPING_PROVENANCE_NOTE
    result["is_official_mts"] = False
    result["is_clinically_approved"] = False
    return result


# ── Inverse mapping: text/number -> acuity ───────────────────────────────────
# Derived FROM MIMIC_ACUITY_TO_MTS above rather than hand-written, so it cannot
# drift from the forward mapping. Hand-maintained copies of this table had
# already diverged (one accepted the float 3.7 as acuity 3, the other rejected
# it) after a single authoring pass, which is exactly the failure this module's
# docstring rule exists to prevent.

def _build_text_index() -> "list[tuple[str, int]]":
    """Longest-token-first index of category words -> acuity.

    Ordering matters: "urgent" is a substring of both "very urgent" and
    "non-urgent", so longer phrases must be tested first or "Non-Urgent (Blue)"
    would be read as acuity 3.
    """
    pairs: list[tuple[str, int]] = []
    for acuity, fields in MIMIC_ACUITY_TO_MTS.items():
        category = str(fields["category"]).lower()
        pairs.append((category, acuity))
        # "Very Urgent (Orange)" -> "very urgent"
        head = category.split("(")[0].strip()
        if head:
            pairs.append((head, acuity))
            if "-" in head:
                pairs.append((head.replace("-", " "), acuity))
        pairs.append((str(fields["colour"]).lower(), acuity))
    # Longest first so "very urgent" beats "urgent" and "non-urgent" beats "urgent".
    return sorted(set(pairs), key=lambda p: (-len(p[0]), p[0]))


_TEXT_INDEX = _build_text_index()
_ACUITY_PATTERN = _re.compile(r"acuity\s*([1-5])\b")
_BARE_DIGIT_PATTERN = _re.compile(r"\b([1-5])\b")


def acuity_from_text(value: Any) -> Optional[int]:
    """Read an acuity 1-5 out of any representation the app produces.

    THE single parser for this. The same clinical acuity reaches different parts
    of the system in different shapes -- ``2`` from a workflow run, ``"Very
    Urgent (Orange)"`` from a human review, ``"Very Urgent (Orange) (acuity 2)"``
    from an override -- and treating those as distinct values is what split one
    acuity across several analytics buckets and broke the drill-down filter.

    Returns None when no acuity can be read. Callers must handle that rather
    than substituting a default: an invented acuity is worse than a missing one.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 5 else None
    if isinstance(value, float):
        # Only an exact integral float is an acuity. 3.7 is not "acuity 3".
        return int(value) if float(value).is_integer() and 1 <= value <= 5 else None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        n = int(text)
        return n if 1 <= n <= 5 else None
    lowered = text.lower()
    match = _ACUITY_PATTERN.search(lowered)
    if match:
        return int(match.group(1))
    for token, acuity in _TEXT_INDEX:
        if token in lowered:
            return acuity
    match = _BARE_DIGIT_PATTERN.search(lowered)
    return int(match.group(1)) if match else None
