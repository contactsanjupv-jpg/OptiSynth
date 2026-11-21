from typing import Optional

from pydantic import BaseModel, Field


class OrganizationUpdateRequest(BaseModel):
    name: str = Field(min_length=0, max_length=300)
    industry: Optional[str] = Field(default=None, max_length=300)
    website: Optional[str] = Field(default=None, max_length=500)


class DataExportRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
