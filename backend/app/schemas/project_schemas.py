"""
NOT RUNTIME-TESTED IN THE SANDBOX: pydantic is not installed here. Syntax-
validated via py_compile only -- see README "Sandbox limitations".
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Direction = Literal["maximize", "minimize"]
ProjectStatus = Literal["draft", "running", "completed"]
ConstraintOp = Literal["<=", ">="]

_MAX_FEATURES = 30


class ConstraintSchema(BaseModel):
    column: str = Field(min_length=1, max_length=100)
    op: ConstraintOp
    value: float

    @field_validator("column")
    @classmethod
    def strip_column(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Constraint column name cannot be empty.")
        return v


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="", max_length=1000)
    target_metric: str = Field(min_length=1, max_length=100)
    direction: Direction
    feature_columns: list[str] = Field(min_length=1, max_length=_MAX_FEATURES)
    constraints: list[ConstraintSchema] = Field(default_factory=list)
    target_value: float | None = None

    @field_validator("name", "objective", "target_metric")
    @classmethod
    def strip_text_fields(cls, v: str) -> str:
        return v.strip()

    @field_validator("feature_columns")
    @classmethod
    def non_empty_columns(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v]
        if not all(cleaned):
            raise ValueError("Feature column names must be non-empty strings.")
        return cleaned


class UpdateStatusRequest(BaseModel):
    status: ProjectStatus


class UpdateProjectRequest(BaseModel):
    """All fields optional -- only the ones actually sent get updated (a
    real PATCH, not a full replace). See services/project_service.py for
    the rule this schema alone can't express: feature_columns and
    constraints are rejected by the SERVICE layer (not here) if the
    project already has experiments, because changing them after
    historical data has been ingested would silently desync the stored
    data from the project's declared shape."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    objective: str | None = Field(default=None, max_length=1000)
    target_metric: str | None = Field(default=None, min_length=1, max_length=100)
    direction: Direction | None = None
    feature_columns: list[str] | None = Field(default=None, min_length=1, max_length=_MAX_FEATURES)
    constraints: list[ConstraintSchema] | None = None
    target_value: float | None = None

    @field_validator("name", "objective", "target_metric")
    @classmethod
    def strip_text_fields(cls, v: str | None) -> str | None:
        return v.strip() if v is not None else None

    @field_validator("feature_columns")
    @classmethod
    def non_empty_columns(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        cleaned = [c.strip() for c in v]
        if not all(cleaned):
            raise ValueError("Feature column names must be non-empty strings.")
        return cleaned


class ProjectResponse(BaseModel):
    id: int
    organization_id: int
    created_by_user_id: int | None
    name: str
    objective: str | None
    target_metric: str
    direction: Direction
    feature_columns: list[str]
    constraints: list[ConstraintSchema]
    target_value: float | None
    status: ProjectStatus
    created_at: str
    updated_at: str
    experiment_count: int | None = None