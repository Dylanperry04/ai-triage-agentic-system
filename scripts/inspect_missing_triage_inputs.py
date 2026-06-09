import json
from app.config import settings
from app.storage.jsonl_repository import read_jsonl


TRIAGE_REQUIRED_FOR_REVIEW = [
    "chiefcomplaint",
    "temperature",
    "heartrate",
    "resprate",
    "o2sat",
    "sbp",
    "dbp",
    "pain",
]


def main():
    path = settings.processed_dir / "triage_input_only_sample.jsonl"

    if not path.exists():
        raise FileNotFoundError(
            "Missing triage input file. Run: python scripts\\build_sample_cases.py --n 100"
        )

    records = read_jsonl(path)

    missing_cases = []

    for record in records:
        missing_fields = [
            field
            for field in TRIAGE_REQUIRED_FOR_REVIEW
            if record.get(field) is None or record.get(field) == ""
        ]

        if missing_fields:
            missing_cases.append({
                "stay_id": record.get("stay_id"),
                "subject_id": record.get("subject_id"),
                "chiefcomplaint": record.get("chiefcomplaint"),
                "missing_fields": missing_fields,
            })

    report = {
        "sample_size": len(records),
        "cases_with_missing_triage_inputs": len(missing_cases),
        "missing_case_percent": round((len(missing_cases) / len(records)) * 100, 2) if records else 0,
        "missing_cases": missing_cases,
    }

    output_path = settings.processed_dir / "missing_triage_inputs_report.json"

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))
    print(f"\nMissing-input report written to: {output_path}")


if __name__ == "__main__":
    main()