from pathlib import Path
from typing import Dict
import pandas as pd

from app.schemas.mimic_ed import MIMIC_ED_FILES, EXPECTED_COLUMNS


def load_table(raw_ed_dir: Path, table: str) -> pd.DataFrame:
    if table not in MIMIC_ED_FILES:
        raise KeyError(f"Unknown table: {table}")

    path = raw_ed_dir / MIMIC_ED_FILES[table]
    if not path.exists():
        raise FileNotFoundError(f"Missing table file: {path}")

    df = pd.read_csv(path, compression="gzip")

    expected = EXPECTED_COLUMNS[table]
    actual = list(df.columns)
    if actual != expected:
        raise ValueError(f"Schema mismatch for {table}. Expected {expected}, got {actual}")

    return df


def load_all_tables(raw_ed_dir: Path) -> Dict[str, pd.DataFrame]:
    return {table: load_table(raw_ed_dir, table) for table in MIMIC_ED_FILES}
