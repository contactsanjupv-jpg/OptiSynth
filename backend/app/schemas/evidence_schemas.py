from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetadataRequest(_Strict):
    """Only the fields sent are changed. Send null or an empty string to clear one."""
    document_type: Optional[str] = None
    source_note: Optional[str] = None
    document_version: Optional[str] = None
    test_method: Optional[str] = None
    document_date: Optional[str] = None
    source_reliability: Optional[str] = None


class ReviewRequest(_Strict):
    action: str = Field(min_length=1, max_length=40)
    reason: Optional[str] = Field(default=None, max_length=2000)
    superseded_by: Optional[int] = None


class ResolveRequest(_Strict):
    resolution: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=2000)
    preferred_dataset_id: Optional[int] = None
