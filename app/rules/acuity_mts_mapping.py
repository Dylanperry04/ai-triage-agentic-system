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
