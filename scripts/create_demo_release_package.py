import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from scripts.export_responsible_ai_evidence import export_evidence_package


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_DIR = PROJECT_ROOT / "release"
PACKAGE_NAME = "ai_triage_agentic_system_demo_release.zip"


EXCLUDE_DIRS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "release",
}

EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
}

EXCLUDE_FILES = {
    ".env",
}


INCLUDE_TOP_LEVEL = {
    "app",
    "frontend",
    "scripts",
    "tests",
    "docs",
    "data",
    "requirements.txt",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
}


def should_exclude(path: Path) -> bool:
    parts = set(path.parts)

    if parts.intersection(EXCLUDE_DIRS):
        return True

    if path.name in EXCLUDE_FILES:
        return True

    if path.suffix in EXCLUDE_SUFFIXES:
        return True

    return False


def should_include(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT)

    if not relative.parts:
        return False

    top_level = relative.parts[0]

    return top_level in INCLUDE_TOP_LEVEL


def write_release_manifest(output_path: Path, evidence_package_path: Path) -> Path:
    manifest = {
        "release_name": "AI Triage Agentic System Demo Release",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "clinical_use_status": "not_for_clinical_use",
        "dataset": "MIMIC-IV-ED Demo v2.2",
        "evidence_package": str(evidence_package_path.relative_to(PROJECT_ROOT)),
        "included_components": [
            "FastAPI backend",
            "Streamlit demo UI",
            "MIMIC-IV-ED Demo processing pipeline",
            "Responsible AI governance report",
            "Human review queue",
            "Evidence export script",
            "Unit tests",
        ],
        "safety_statement": (
            "This release is a research/demo artifact only. "
            "It does not assign Manchester triage categories, does not provide diagnosis, "
            "and must not be used for patient care."
        ),
    }

    manifest_path = output_path / "release_manifest.json"

    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest_path


def create_release_zip() -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    evidence_package_path = export_evidence_package()
    manifest_path = write_release_manifest(RELEASE_DIR, evidence_package_path)

    zip_path = RELEASE_DIR / PACKAGE_NAME

    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for path in PROJECT_ROOT.rglob("*"):
            if path.is_dir():
                continue

            if should_exclude(path):
                continue

            if not should_include(path):
                continue

            relative_path = path.relative_to(PROJECT_ROOT)
            zipf.write(path, relative_path)

        zipf.write(manifest_path, manifest_path.relative_to(PROJECT_ROOT))

    return zip_path


def main():
    zip_path = create_release_zip()

    print("Demo release package created.")
    print(f"Output: {zip_path}")
    print()
    print("This package is for research/demo review only.")
    print("It is not for clinical use.")


if __name__ == "__main__":
    main()