from app.config import settings
from app.data_pipeline.download import download_mimic_ed_demo


if __name__ == "__main__":
    # BUG FIX (found during third-party code review): this previously used
    # settings.raw_ed_dir, which resolves to the FULL MIMIC-IV-ED path
    # (mimic-iv-ed/2.2/ed), not the demo path. This script downloads the
    # DEMO dataset, so it must use settings.raw_demo_dir
    # (mimic-iv-ed-demo/2.2/ed) -- otherwise demo files would silently be
    # written into the directory meant for the full, credentialed dataset.
    report = download_mimic_ed_demo(settings.raw_demo_dir)
    print("Downloaded MIMIC-IV-ED Demo v2.2 files and verified column headers:")
    print("(this checks each file's CSV header row against an expected column")
    print("list -- it does not compute or compare a SHA256 checksum; see")
    print("README.md's MIMIC-IV-ED Demo v2.2 section if you need that stronger")
    print("guarantee)")
    for table, columns in report.items():
        print(f"- {table}: {len(columns)} columns")
