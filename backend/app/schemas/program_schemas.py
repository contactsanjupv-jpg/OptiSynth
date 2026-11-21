from typing import Optional

from pydantic import BaseModel, Field


class ChangeStageRequest(BaseModel):
    to_stage: str = Field(min_length=1, max_length=40)
    reason: Optional[str] = Field(default=None, max_length=2000)
