import argparse
import json

from app.config import settings
from app.data_pipeline.loaders import load_all_tables
from app.data_pipeline.validation import validate_loaded_tables
from app.data_pipeline.mapping import build_cases
from app.data_pipeline.export import export_cases


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=25, help="Number of ED stays to sample")
    args = parser.parse_args()

    tables = load_all_tables(settings.raw_ed_dir)
    report = validate_loaded_tables(tables)
    cases = build_cases(tables, n=args.n)
    export_cases(cases, settings.processed_dir)

    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    with (settings.processed_dir / "schema_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"Built {len(cases)} cases.")
    print(f"Wrote outputs to {settings.processed_dir}")


if __name__ == "__main__":
    main()
