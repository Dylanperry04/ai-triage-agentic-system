from typing import List
from pydantic import BaseModel, Field


class DataValidationResult(BaseModel):
    validation_status: str
    missing_required_fields: List[str] = Field(default_factory=list)
    non_informative_fields: List[str] = Field(default_factory=list)
    requires_human_data_review: bool = True
    notes: List[str] = Field(default_factory=list)
