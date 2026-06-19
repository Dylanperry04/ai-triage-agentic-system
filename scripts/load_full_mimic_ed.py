"""
Load Full MIMIC-IV-ED Dataset.

Use this script once you have PhysioNet credentials and your data
access request for MIMIC-IV-ED has been approved.

MIMIC-IV-ED full dataset:
  ~216,000 ED stays (vs 100 in the demo)
  Same six-table structure as the demo
  Requires credentialed PhysioNet account

Steps to get access:
  1. Create an account at https://physionet.org/
  2. Complete the required CITI training
  3. Request access to MIMIC-IV-ED at:
     https://physionet.org/content/mimic-iv-ed/2.2/
  4. Once approved, run this script with your credentials

Usage:
  python scripts/load_full_mimic_ed.py --username YOUR_PHYSIONET_USERNAME

The script will:
  1. Download all six .csv.gz files to data/raw/mimic-iv-ed/2.2/ed/
  2. Verify column headers against the expected schema
  3. Build sample cases (first 500 stays for initial validation)
  4. Build outcome labels (real MIMIC admission/acuity outcomes)
  5. Train ML models on the full dataset

IMPORTANT: Do not commit data/raw/ to git. The .gitignore already
blocks this. Patient data must stay on your local machine or secured
Azure storage only.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings
from app.schemas.mimic_ed import MIMIC_ED_FILES, EXPECTED_COLUMNS, MIMIC_ED_FULL_BASE_URL


def download_with_credentials(username: str, password: str) -> None:
    """
    Download full MIMIC-IV-ED files using PhysioNet credentials.
    PhysioNet uses HTTP Basic Auth for credentialed downloads.
    """
    import requests

    raw_dir = settings.raw_ed_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading MIMIC-IV-ED full dataset to: {raw_dir}")
    print(f"Base URL: {MIMIC_ED_FULL_BASE_URL}")
    print("This may take several minutes depending on your connection.\n")

    for table, filename in MIMIC_ED_FILES.items():
        dest = raw_dir / filename
        if dest.exists():
            print(f"  ✓ {filename} already exists — skipping")
            continue

        url = f"{MIMIC_ED_FULL_BASE_URL}/{filename}"
        print(f"  Downloading {filename}...")

        with requests.get(
            url,
            auth=(username, password),
            stream=True,
            timeout=300,
        ) as resp:
            if resp.status_code == 401:
                raise RuntimeError(
                    "Authentication failed. Check your PhysioNet username and password."
                )
            if resp.status_code == 403:
                raise RuntimeError(
                    "Access denied. Make sure your data access request for "
                    "MIMIC-IV-ED has been approved at physionet.org."
                )
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            pct = 100 * downloaded / total
                            print(f"\r    {pct:.0f}%", end="", flush=True)
            print(f"\r  ✓ {filename} ({downloaded / 1e6:.1f} MB)")

    print("\nVerifying column headers...")
    from app.data_pipeline.download import verify_downloaded_headers
    report = verify_downloaded_headers(raw_dir)
    for table, cols in report.items():
        expected = EXPECTED_COLUMNS[table]
        status = "✓" if cols == expected else "✗ MISMATCH"
        print(f"  {status} {table}: {len(cols)} columns")

    print("\n✅ Download and verification complete.")


def build_full_dataset_pipeline(n_cases: int = 1000) -> None:
    """Build cases, generate labels, train models on the full dataset."""
    import subprocess

    steps = [
        (
            "Building case files",
            [sys.executable, "scripts/build_sample_cases.py",
             "--n", str(n_cases), "--dataset", "full"],
        ),
        (
            "Building outcome labels (real MIMIC outcomes)",
            [sys.executable, "scripts/build_outcome_labels.py",
             "--n", str(n_cases)],
        ),
        (
            "Training ML models",
            [sys.executable, "ml_training/train_all_models.py"],
        ),
    ]

    for description, cmd in steps:
        print(f"\n→ {description}...")
        result = subprocess.run(cmd, cwd=PROJECT_ROOT)
        if result.returncode != 0:
            print(f"  ✗ {description} failed (exit code {result.returncode})")
            sys.exit(1)
        print(f"  ✓ {description} complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download and set up full MIMIC-IV-ED dataset."
    )
    parser.add_argument(
        "--username",
        type=str,
        required=True,
        help="Your PhysioNet username",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=1000,
        help="Number of cases to build initially (default: 1000; use -1 for all)",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download files only; skip case building and training",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("MIMIC-IV-ED Full Dataset Setup")
    print("=" * 60)
    print(
        "\n⚠️  PATIENT DATA NOTICE:\n"
        "You are about to download real patient data. This data must:\n"
        "  • Stay on your local machine or secured Azure storage\n"
        "  • Never be committed to git (data/raw/ is in .gitignore)\n"
        "  • Never be shared without PhysioNet authorisation\n"
        "  • Be handled according to your data access agreement\n"
    )
    confirm = input("Type 'yes' to confirm you understand these obligations: ")
    if confirm.strip().lower() != "yes":
        print("Aborted.")
        sys.exit(0)

    password = getpass.getpass(f"PhysioNet password for {args.username}: ")

    download_with_credentials(args.username, password)

    if not args.download_only:
        n = args.n if args.n > 0 else 999_999
        build_full_dataset_pipeline(n)

    print(
        "\n✅ Full MIMIC-IV-ED setup complete.\n"
        "Update ACTIVE_DATASET=full in your .env to switch the app to full data.\n"
        "Run: streamlit run frontend/app.py"
    )
