"""Regenerate data/demo/azure_supervisor_demo_cases.jsonl (synthetic supervisor-demo cohort).

60 deterministic, clearly-marked synthetic MIMIC-shaped cases for the Azure
supervisor demo profile only. Not real MIMIC, not real patients. `edstay.intime`
is stamped relative to generation time (staggered 15 min – 7 h before) so the
overdue-vitals sweeper (210-minute threshold, app/api/case_routes.py) can
demonstrate the real recheck-notification cycle on actioned cases.

Run:  python scripts/generate_supervisor_demo_cases.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(4242)
OUT = Path(__file__).resolve().parent.parent / "data" / "demo" / "azure_supervisor_demo_cases.jsonl"

SCEN = [
    ("chest_pain_radiating", "chest pain radiating to left arm", 2, (100, 128), (20, 26), (90, 95), (96, 118), "AMBULANCE"),
    ("shortness_of_breath", "shortness of breath at rest", 2, (104, 126), (22, 28), (88, 94), (100, 124), "AMBULANCE"),
    ("severe_headache_sudden", "sudden severe headache, worst of life", 2, (92, 118), (18, 24), (95, 99), (138, 178), "AMBULANCE"),
    ("staggered_overdose", "staggered paracetamol overdose", 2, (96, 120), (16, 22), (95, 99), (102, 126), "WALK IN"),
    ("gi_bleed_melaena", "black stools and dizziness", 2, (104, 128), (18, 24), (93, 98), (88, 108), "AMBULANCE"),
    ("abdominal_pain_rif", "right lower abdominal pain and fever", 3, (88, 112), (16, 22), (95, 99), (108, 138), "WALK IN"),
    ("asthma_exacerbation", "asthma flare, speaking in phrases", 3, (96, 118), (20, 26), (92, 96), (112, 140), "WALK IN"),
    ("fall_hip_pain", "fall at home, right hip pain, cannot weight-bear", 3, (78, 102), (16, 20), (95, 99), (118, 152), "AMBULANCE"),
    ("renal_colic", "severe left flank pain with vomiting", 3, (86, 110), (16, 22), (96, 100), (118, 150), "WALK IN"),
    ("laceration_forearm", "deep forearm laceration, bleeding controlled", 3, (76, 100), (14, 20), (97, 100), (112, 142), "WALK IN"),
    ("confusion_elderly", "new confusion, possible urinary infection", 3, (84, 110), (16, 22), (93, 98), (104, 140), "AMBULANCE"),
    ("dizziness_presyncope", "dizziness", 3, (58, 96), (14, 20), (95, 100), (96, 130), "WALK IN"),
    ("febrile_cough", "productive cough and fever for three days", 4, (72, 96), (14, 20), (95, 99), (110, 140), "WALK IN"),
    ("ankle_injury", "twisted ankle with swelling", 4, (64, 88), (12, 18), (97, 100), (112, 142), "WALK IN"),
    ("hand_laceration_minor", "small hand laceration, dressing in place", 4, (62, 86), (12, 18), (97, 100), (110, 140), "WALK IN"),
    ("uti_symptoms", "burning on urination, two days", 4, (66, 90), (12, 18), (97, 100), (108, 138), "WALK IN"),
    ("back_pain_chronic", "chronic back pain, worse this week", 5, (60, 84), (12, 16), (97, 100), (112, 144), "WALK IN"),
    ("medication_review", "repeat prescription issue", 5, (58, 82), (12, 16), (97, 100), (110, 142), "WALK IN"),
    ("dressing_change", "wound review and dressing change", 5, (58, 80), (12, 16), (97, 100), (108, 138), "WALK IN"),
]


def pain_for(ac: int) -> str:
    return {1: "9", 2: str(random.choice([7, 8, 9])), 3: str(random.choice([4, 5, 6, 7])),
            4: str(random.choice([2, 3, 4, 5])), 5: str(random.choice([0, 1, 2, 3]))}[ac]


def main() -> None:
    rows = [
        ("cardiac_arrest_rosc", "post-arrest, return of circulation", 1, (110, 132), (24, 30), (85, 92), (82, 100), "AMBULANCE"),
        ("major_trauma_rtc", "road traffic collision, multiple injuries", 1, (112, 134), (24, 30), (88, 94), (84, 104), "AMBULANCE"),
    ]
    # Guaranteed scenario coverage asserted by tests/test_azure_supervisor_demo.py
    # and tests/test_deployment_packaging.py: exact chief complaints
    # "chest pain radiating to left arm" and "dizziness" must exist.
    rows.append(SCEN[0])                                   # chest pain radiating (acuity 2)
    rows.append(next(s for s in SCEN if s[1] == "dizziness"))  # dizziness (acuity 3)
    counts = {2: 11, 3: 17, 4: 16, 5: 12}
    while sum(counts.values()) > 0:
        s = random.choice(SCEN)
        if counts.get(s[2], 0) <= 0:
            continue
        counts[s[2]] -= 1
        rows.append(s)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    out = []
    sid = 990000001
    for i, (scen, cc, ac, hr, rr, o2, sbp, transport) in enumerate(rows):
        stay = sid + i
        hrv = round(random.uniform(*hr), 1); rrv = round(random.uniform(*rr), 1)
        o2v = round(random.uniform(*o2), 1); sbpv = round(random.uniform(*sbp), 1)
        dbpv = round(sbpv - random.uniform(28, 48), 1)
        tf = round(random.uniform(97.0, 101.8 if ac <= 3 else 99.4), 1)
        # Staggered arrival: 15 min – 7 h before generation. Older arrivals let
        # the real 210-minute overdue-vitals sweeper fire during a demo.
        intime = (now - timedelta(minutes=random.randint(15, 420))).isoformat()
        out.append({
            "source_dataset": "MIMIC-IV-ED-Synthetic-Supervisor-Demo",
            "stay_id": stay, "subject_id": stay, "synthetic_demo": True,
            "demo_data_notice": "Synthetic supervisor-demo case. Not real MIMIC and not real patient data.",
            "demo_scenario": scen,
            "edstay": {"subject_id": stay, "stay_id": stay, "gender": random.choice(["F", "M"]),
                       "arrival_transport": transport, "disposition": "SYNTHETIC", "intime": intime},
            "triage": {"subject_id": stay, "stay_id": stay, "temperature": tf, "temperature_unit": "F",
                       "heartrate": hrv, "resprate": rrv, "o2sat": o2v, "sbp": sbpv, "dbp": dbpv,
                       "pain": pain_for(ac), "chiefcomplaint": cc, "acuity": ac},
            "vitals_timeseries": [], "diagnoses": [], "medrecon": [], "pyxis": [],
        })
    with OUT.open("w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(out)} cases -> {OUT}")


if __name__ == "__main__":
    main()
