from pathlib import Path
import orjson


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    records: list[dict] = []
    with path.open("rb") as f:
        for line in f:
            if line.strip():
                records.append(orjson.loads(line))
    return records
