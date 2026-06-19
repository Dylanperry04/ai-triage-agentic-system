"""Build KTAS research labels from processed Kaggle cases.

Main target: KTAS_expert (1-5). Secondary target: emergency/non-emergency,
where KTAS 1-3 is emergency and KTAS 4-5 is non-emergency according to the
Kaggle dataset documentation. No Manchester mapping is produced.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings


def build_ktas_labels(cases_path: Path, output_path: Path, n: int | None = None) -> dict:
    if not cases_path.exists():
        raise FileNotFoundError(
            f"Cases file not found: {cases_path}\n"
            "Run: python scripts/build_sample_cases.py --dataset kaggle_ktas"
        )

    records = []
    with cases_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    if n is not None:
        records = records[:n]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped_no_label = 0
    counts = Counter()

    with output_path.open("w", encoding="utf-8") as out:
        for rec in records:
            triage = rec.get("triage") or {}
            edstay = rec.get("edstay") or {}
            meta = rec.get("retrospective_metadata") or {}
            ktas_expert = meta.get("ktas_expert")
            if ktas_expert is None:
                skipped_no_label += 1
                continue

            ktas_expert = int(ktas_expert)
            ktas_rn = meta.get("ktas_rn")
            ktas_rn = None if ktas_rn is None else int(ktas_rn)
            emergency = int(ktas_expert <= 3)
            high_acuity = int(ktas_expert <= 2)
            mistriage = meta.get("mistriage")

            row = {
                "stay_id": rec.get("stay_id"),
                "subject_id": rec.get("subject_id"),
                "source_dataset": rec.get("source_dataset"),

                # Triage-time features only
                "age": triage.get("age"),
                "gender": edstay.get("gender"),
                "group_code": triage.get("group_code"),
                "patients_per_hour": triage.get("patients_per_hour"),
                "arrival_transport": edstay.get("arrival_transport"),
                "arrival_mode_code": triage.get("arrival_mode_code"),
                "injury_code": triage.get("injury_code"),
                "mental_code": triage.get("mental_code"),
                "chiefcomplaint": triage.get("chiefcomplaint"),
                "temperature": triage.get("temperature"),
                "temperature_unit": triage.get("temperature_unit", "C"),
                "heartrate": triage.get("heartrate"),
                "resprate": triage.get("resprate"),
                "o2sat": triage.get("o2sat"),
                "sbp": triage.get("sbp"),
                "dbp": triage.get("dbp"),
                "pain": triage.get("pain"),
                "pain_present": triage.get("pain_present"),
                "nrs_pain": triage.get("nrs_pain"),

                # Labels/audit fields, never model inputs unless an audit mode explicitly says so
                "label_ktas_expert": ktas_expert,
                "label_ktas_emergency": emergency,
                "label_ktas_high_acuity": high_acuity,
                "label_ktas_rn": ktas_rn,
                "label_mistriage": mistriage,
                "label_mistriage_name": meta.get("mistriage_label"),
                "label_error_group": meta.get("error_group"),
                "label_source": "Kaggle-KTAS-expert-field",
                "research_label_only": True,
                "requires_clinical_validation": True,
                "label_note": (
                    "KTAS_expert is used as the research target for this public dataset phase. "
                    "It is not Manchester triage and is not mapped to Manchester. "
                    "KTAS_RN, mistriage, diagnosis, disposition, length of stay, and KTAS duration "
                    "are excluded from triage-support model features."
                ),
            }
            out.write(json.dumps(row, default=str) + "\n")
            written += 1
            counts[f"ktas_{ktas_expert}"] += 1
            counts["emergency" if emergency else "non_emergency"] += 1
            if mistriage is not None:
                counts[f"mistriage_{mistriage}"] += 1

    summary = {
        "dataset": "Kaggle-KTAS",
        "total_cases": len(records),
        "written": written,
        "skipped_no_ktas_expert": skipped_no_label,
        "label_counts": dict(counts),
        "output": str(output_path),
        "safety_note": (
            "Labels are KTAS research targets. No Manchester category is created. "
            "This output is not for clinical use."
        ),
    }
    print(json.dumps(summary, indent=2, default=str))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build KTAS labels from processed cases.")
    parser.add_argument("--cases", type=Path, default=settings.processed_dir / "triage_cases_sample.jsonl")
    parser.add_argument("--out", type=Path, default=settings.processed_dir / "ktas_labels.jsonl")
    parser.add_argument("--n", type=int, default=None)
    args = parser.parse_args()
    build_ktas_labels(args.cases, args.out, args.n)
