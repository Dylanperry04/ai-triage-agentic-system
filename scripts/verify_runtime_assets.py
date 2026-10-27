"""Validate the Azure ZIP manifest without early-closing shell pipelines."""
from pathlib import Path
import sys
import zipfile


REQUIRED = {
    ".python_packages/lib/site-packages/uvicorn/__init__.py",
    ".python_packages/lib/site-packages/fastapi/__init__.py",
    ".python_packages/lib/site-packages/catboost/__init__.py",
    "data/uhl_demo_100.json",
    "artifacts/model/uhl_synthetic_acuity_selected.joblib",
}


def verify_runtime_assets(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    missing = REQUIRED - names
    if missing:
        raise ValueError("Missing runtime assets: " + ", ".join(sorted(missing)))
    if "data/uhl_dataset_final.csv.gz" in names:
        raise ValueError("Full training cohort must not enter the 100-case deployment")


if __name__ == "__main__":
    verify_runtime_assets(Path(sys.argv[1]))
    print("PASS: runtime assets present and full training cohort excluded")
