from pathlib import Path
import gzip
from typing import Dict
import requests

from app.schemas.mimic_ed import MIMIC_ED_DEMO_BASE_URL, MIMIC_ED_FILES, EXPECTED_COLUMNS


class DownloadError(RuntimeError):
    pass


def download_file(url: str, dest: Path, timeout: int = 60) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as response:
        if response.status_code != 200:
            raise DownloadError(f"Failed to download {url}. HTTP status: {response.status_code}")
        with dest.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def read_gzip_header(path: Path) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        header = f.readline().strip()
    return header.split(",")


def verify_downloaded_headers(raw_ed_dir: Path) -> Dict[str, list[str]]:
    report: Dict[str, list[str]] = {}
    for table, filename in MIMIC_ED_FILES.items():
        path = raw_ed_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing expected file: {path}")
        actual = read_gzip_header(path)
        expected = EXPECTED_COLUMNS[table]
        if actual != expected:
            raise ValueError(
                f"Schema mismatch for {table}.\n"
                f"Expected: {expected}\n"
                f"Actual:   {actual}"
            )
        report[table] = actual
    return report


def download_mimic_ed_demo(raw_ed_dir: Path, overwrite: bool = False) -> Dict[str, list[str]]:
    raw_ed_dir.mkdir(parents=True, exist_ok=True)

    for table, filename in MIMIC_ED_FILES.items():
        url = f"{MIMIC_ED_DEMO_BASE_URL}/{filename}"
        dest = raw_ed_dir / filename
        if dest.exists() and not overwrite:
            continue
        download_file(url, dest)

    return verify_downloaded_headers(raw_ed_dir)
