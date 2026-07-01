"""Fail if a checkpoint archive contains runtime logs, caches, model binaries, or
credentialed MIMIC tables.

Usage:
    python scripts/check_package_hygiene.py path/to/checkpoint.zip
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path


FORBIDDEN_PATTERNS = [
    re.compile(r"(^|[\\/])data[\\/]processed[\\/].+\.(json|jsonl)$"),
    re.compile(r"(^|[\\/])__pycache__([\\/]|$)"),
    re.compile(r"(^|[\\/])\.pytest_cache([\\/]|$)"),
    re.compile(r"\.(joblib|pkl)$", re.IGNORECASE),
    re.compile(
        r"(^|[\\/])(edstays|triage|vitalsign|diagnosis|medrecon|pyxis)\.csv(\.gz)?$",
        re.IGNORECASE,
    ),
]


def forbidden_entries(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
    return [
        name
        for name in names
        if any(pattern.search(name) for pattern in FORBIDDEN_PATTERNS)
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/check_package_hygiene.py path/to/checkpoint.zip", file=sys.stderr)
        return 2
    zip_path = Path(argv[1])
    if not zip_path.exists():
        print(f"Archive not found: {zip_path}", file=sys.stderr)
        return 2
    bad = forbidden_entries(zip_path)
    if bad:
        print("Forbidden archive entries detected:", file=sys.stderr)
        for name in bad:
            print(f"  {name}", file=sys.stderr)
        return 1
    print(f"PASS: {zip_path} contains no forbidden runtime/data/cache entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
