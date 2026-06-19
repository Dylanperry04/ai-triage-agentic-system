from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from collections import Counter
from app.config import settings
from app.storage.jsonl_repository import read_jsonl


def main():
    cases_path = settings.processed_dir / "triage_cases_sample.jsonl"
    if not cases_path.exists():
        raise FileNotFoundError("No processed sample found. Run: python scripts/run_ktas_pipeline.py")
    cases = read_jsonl(cases_path)
    triage_inputs = []
    retrospective_labels = []
    for case in cases:
        triage = case.get("triage") or {}
        edstay = case.get("edstay") or {}
        meta = case.get("retrospective_metadata") or {}
        triage_inputs.append({
            "subject_id": case.get("subject_id"),
            "stay_id": case.get("stay_id"),
            "source_dataset": case.get("source_dataset"),
            "gender": edstay.get("gender"),
            "age": triage.get("age"),
            "arrival_transport": edstay.get("arrival_transport"),
            "chiefcomplaint": triage.get("chiefcomplaint"),
            "mental_code": triage.get("mental_code"),
            "temperature": triage.get("temperature"),
            "temperature_unit": triage.get("temperature_unit"),
            "heartrate": triage.get("heartrate"),
            "resprate": triage.get("resprate"),
            "o2sat": triage.get("o2sat"),
            "sbp": triage.get("sbp"),
            "dbp": triage.get("dbp"),
            "pain_present": triage.get("pain_present"),
            "nrs_pain": triage.get("nrs_pain"),
        })
        retrospective_labels.append({
            "stay_id": case.get("stay_id"),
            "ktas_rn": meta.get("ktas_rn"),
            "ktas_expert": meta.get("ktas_expert"),
            "mistriage": meta.get("mistriage"),
            "error_group": meta.get("error_group"),
            "disposition": meta.get("disposition_label"),
            "diagnosis_in_ed": meta.get("diagnosis_in_ed"),
            "length_of_stay_min": meta.get("length_of_stay_min"),
            "ktas_duration_min": meta.get("ktas_duration_min"),
        })
    def missing_report(rows):
        if not rows:
            return {}
        out = {}
        for field in rows[0].keys():
            count = sum(1 for r in rows if r.get(field) is None or r.get(field) == "")
            out[field] = {"missing_count": count, "missing_percent": round(count / len(rows) * 100, 2)}
        return out
    report = {
        "dataset": "Kaggle-KTAS",
        "sample_size": len(cases),
        "triage_input_fields": list(triage_inputs[0].keys()) if triage_inputs else [],
        "retrospective_label_fields": list(retrospective_labels[0].keys()) if retrospective_labels else [],
        "missingness_triage_inputs": missing_report(triage_inputs),
        "top_chief_complaints": Counter(r["chiefcomplaint"] for r in triage_inputs if r.get("chiefcomplaint")).most_common(20),
        "ktas_expert_distribution": Counter(str(r["ktas_expert"]) for r in retrospective_labels if r.get("ktas_expert") is not None).most_common(),
        "ktas_rn_distribution": Counter(str(r["ktas_rn"]) for r in retrospective_labels if r.get("ktas_rn") is not None).most_common(),
        "mistriage_distribution": Counter(str(r["mistriage"]) for r in retrospective_labels if r.get("mistriage") is not None).most_common(),
        "leakage_policy": "Retrospective labels are excluded from TriageTimeInput and model features.",
        "manchester_policy": "No KTAS-to-Manchester mapping is implemented.",
    }
    output_path = settings.processed_dir / "dataset_audit_report.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"Dataset audit written to: {output_path}")
    print(f"Sample size: {report.get('sample_size')}")


if __name__ == "__main__":
    main()
