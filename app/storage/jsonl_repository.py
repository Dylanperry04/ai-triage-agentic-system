from pathlib import Path

from app.storage.jsonl_io import read_jsonl_dicts


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    return read_jsonl_dicts(path)
