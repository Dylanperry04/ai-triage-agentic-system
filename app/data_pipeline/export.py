from pathlib import Path
from typing import Iterable
import orjson

from app.schemas.internal import EDTriageCase


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        for record in records:
            f.write(orjson.dumps(record))
            f.write(b"\n")


def export_cases(cases: list[EDTriageCase], processed_dir: Path) -> None:
    processed_dir.mkdir(parents=True, exist_ok=True)

    write_jsonl(
        processed_dir / "triage_cases_sample.jsonl",
        [case.model_dump(mode="json") for case in cases],
    )

    write_jsonl(
        processed_dir / "triage_input_only_sample.jsonl",
        [case.to_triage_time_input().model_dump(mode="json") for case in cases],
    )

    write_jsonl(
        processed_dir / "retrospective_labels_sample.jsonl",
        [case.to_retrospective_labels().model_dump(mode="json") for case in cases],
    )
