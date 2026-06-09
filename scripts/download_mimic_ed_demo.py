from app.config import settings
from app.data_pipeline.download import download_mimic_ed_demo


if __name__ == "__main__":
    report = download_mimic_ed_demo(settings.raw_ed_dir)
    print("Downloaded and verified MIMIC-IV-ED Demo v2.2 files:")
    for table, columns in report.items():
        print(f"- {table}: {len(columns)} columns")
