from app.config import settings
from app.data_pipeline.download import verify_downloaded_headers


if __name__ == "__main__":
    report = verify_downloaded_headers(settings.raw_ed_dir)
    print("Schema verification passed.")
    for table, columns in report.items():
        print(f"- {table}: {columns}")
