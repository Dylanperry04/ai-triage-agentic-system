import orjson

from app.config import settings

VALID_REVIEW_PATH = settings.processed_dir / "human_reviews.jsonl"


def main():
    if not VALID_REVIEW_PATH.exists():
        print("No human_reviews.jsonl file found.")
        return

    valid_records = []

    with VALID_REVIEW_PATH.open("rb") as f:
        for line in f:
            if not line.strip():
                continue

            record = orjson.loads(line)

            if int(record.get("stay_id", -1)) > 0:
                valid_records.append(record)

    with VALID_REVIEW_PATH.open("wb") as f:
        for record in valid_records:
            f.write(orjson.dumps(record))
            f.write(b"\n")

    print(f"Kept {len(valid_records)} valid review records.")
    print("Removed records with invalid stay_id values such as 0.")


if __name__ == "__main__":
    main()