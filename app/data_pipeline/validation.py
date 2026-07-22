from typing import Dict, Any
import pandas as pd
from app.schemas.mimic_ed import EXPECTED_COLUMNS


def validate_loaded_tables(tables: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    report: Dict[str, Any] = {"tables": {}}

    for table_name, expected_cols in EXPECTED_COLUMNS.items():
        if table_name not in tables:
            raise ValueError(f"Missing loaded table: {table_name}")

        df = tables[table_name]
        actual_cols = list(df.columns)

        if actual_cols != expected_cols:
            raise ValueError(
                f"Schema mismatch for {table_name}. "
                f"Expected {expected_cols}; got {actual_cols}"
            )

        report["tables"][table_name] = {
            "row_count": int(len(df)),
            "column_count": int(len(df.columns)),
            "columns": actual_cols,
            "missing_by_column": {
                col: int(df[col].isna().sum()) for col in actual_cols
            },
        }

    return report
