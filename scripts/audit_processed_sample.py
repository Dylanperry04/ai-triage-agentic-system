import json
from pathlib import Path
from collections import Counter

from app.config import settings
from app.storage.jsonl_repository import read_jsonl


def summarise_missing(records, section_name):
    counts = Counter()
    total = len(records)

    for record in records:
        section = record.get(section_name, {})
        if section is None:
            continue

        for key, value in section.items():
            if value is None or value == "":
                counts[key] += 1

    return {
        key: {
            "missing_count": count,
            "missing_percent": round((count / total) * 100, 2) if total else 0,
        }
        for key, count in counts.items()
    }


def main():
    processed_dir = settings.processed_dir
    cases_path = processed_dir / "triage_cases_sample.jsonl"

    if not cases_path.exists():
        raise FileNotFoundError(
            "No processed sample found. Run: python scripts\\build_sample_cases.py --n 25"
        )

    cases = read_jsonl(cases_path)

    triage_inputs = []
    retrospective_labels = []

    for case in cases:
        triage = case.get("triage")
        edstay = case.get("edstay")

        triage_inputs.append({
            "subject_id": case.get("subject_id"),
            "stay_id": case.get("stay_id"),
            "intime": edstay.get("intime") if edstay else None,
            "gender": edstay.get("gender") if edstay else None,
            "race": edstay.get("race") if edstay else None,
            "arrival_transport": edstay.get("arrival_transport") if edstay else None,
            "chiefcomplaint": triage.get("chiefcomplaint") if triage else None,
            "temperature": triage.get("temperature") if triage else None,
            "heartrate": triage.get("heartrate") if triage else None,
            "resprate": triage.get("resprate") if triage else None,
            "o2sat": triage.get("o2sat") if triage else None,
            "sbp": triage.get("sbp") if triage else None,
            "dbp": triage.get("dbp") if triage else None,
            "pain": triage.get("pain") if triage else None,
        })

        retrospective_labels.append({
            "stay_id": case.get("stay_id"),
            "original_acuity": triage.get("acuity") if triage else None,
            "disposition": edstay.get("disposition") if edstay else None,
            "diagnosis_count": len(case.get("diagnoses", [])),
            "vitals_timeseries_count": len(case.get("vitals_timeseries", [])),
            "medrecon_count": len(case.get("medrecon", [])),
            "pyxis_count": len(case.get("pyxis", [])),
        })

    chief_complaints = Counter(
        item["chiefcomplaint"] for item in triage_inputs if item["chiefcomplaint"]
    )

    dispositions = Counter(
        item["disposition"] for item in retrospective_labels if item["disposition"]
    )

    acuities = Counter(
        str(item["original_acuity"])
        for item in retrospective_labels
        if item["original_acuity"] is not None
    )

    report = {
        "sample_size": len(cases),
        "triage_input_fields": list(triage_inputs[0].keys()) if triage_inputs else [],
        "retrospective_label_fields": list(retrospective_labels[0].keys()) if retrospective_labels else [],
        "missingness_triage_inputs": {
            field: {
                "missing_count": sum(
                    1 for item in triage_inputs if item.get(field) is None or item.get(field) == ""
                ),
                "missing_percent": round(
                    sum(1 for item in triage_inputs if item.get(field) is None or item.get(field) == "") / len(triage_inputs) * 100,
                    2,
                ) if triage_inputs else 0,
            }
            for field in triage_inputs[0].keys()
        } if triage_inputs else {},
        "top_chief_complaints": chief_complaints.most_common(20),
        "disposition_distribution": dispositions.most_common(),
        "original_acuity_distribution": acuities.most_common(),
        "retrospective_record_counts": {
            "total_diagnoses": sum(item["diagnosis_count"] for item in retrospective_labels),
            "total_vitals_timeseries_records": sum(item["vitals_timeseries_count"] for item in retrospective_labels),
            "total_medrecon_records": sum(item["medrecon_count"] for item in retrospective_labels),
            "total_pyxis_records": sum(item["pyxis_count"] for item in retrospective_labels),
        },
    }

    output_path = processed_dir / "dataset_audit_report.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Dataset audit written to: {output_path}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()