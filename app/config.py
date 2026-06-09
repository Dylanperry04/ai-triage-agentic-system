from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    project_root: Path = Path(__file__).resolve().parents[1]
    data_root: Path = project_root / "data"
    raw_ed_dir: Path = data_root / "raw" / "mimic-iv-ed-demo" / "2.2" / "ed"
    processed_dir: Path = data_root / "processed"


settings = Settings()
