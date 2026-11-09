"""Convert a clean Windows CPython 3.11 pip resolution report to a wheel lock."""

import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit


def build_lock(report):
    environment = report["environment"]
    expected = {
        "implementation_name": "cpython",
        "python_version": "3.11",
        "sys_platform": "win32",
        "platform_machine": "AMD64",
    }
    if any(environment.get(key) != value for key, value in expected.items()):
        raise ValueError("Resolve the lock on Windows x64 with CPython 3.11")
    packages = {}
    for item in report["install"]:
        metadata = item["metadata"]
        download = item["download_info"]
        url = urlsplit(download["url"])
        digest = download["archive_info"]["hashes"]["sha256"]
        name = re.sub(r"[-_.]+", "-", metadata["name"]).lower()
        if (url.scheme != "https" or url.hostname != "files.pythonhosted.org"
                or not url.path.endswith(".whl") or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or name in packages):
            raise ValueError(f"Unexpected wheel source or digest for {name}")
        extras = item.get("requested_extras") or []
        suffix = f"[{','.join(sorted(extras))}]" if extras else ""
        packages[name] = f"{name}{suffix}=={metadata['version']} --hash=sha256:{digest}"
    if not packages:
        raise ValueError("The pip report did not contain a runtime resolution")
    return "\n".join([
        "# Windows x64, CPython 3.11 runtime wheels for Run-Local-Review.ps1.",
        "# Generated with scripts/lock_local_runtime.py. See LOCAL_REVIEW.md.",
        "--index-url https://pypi.org/simple",
        "--require-hashes",
        "--only-binary=:all:",
        *[packages[name] for name in sorted(packages)],
        "",
    ])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("lock", type=Path)
    args = parser.parse_args()
    args.lock.write_text(build_lock(json.loads(args.report.read_text(encoding="utf-8"))), encoding="utf-8")
