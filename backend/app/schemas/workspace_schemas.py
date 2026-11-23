from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DetailsRequest(_Strict):
    """Only the fields sent are changed. Send null or an empty string to clear one."""
    material_replaced: Optional[str] = None
    reason_for_substitution: Optional[str] = None
    business_impact: Optional[str] = None
    original_formulation: Optional[str] = None
    manufacturing_constraints: Optional[str] = None
    target_date: Optional[str] = None


class OwnerRequest(_Strict):
    user_id: Optional[int] = None


class ReviewerRequest(_Strict):
    user_id: int


class RequirementRequest(_Strict):
    property: str = Field(min_length=1, max_length=200)
    threshold: float
    direction: str
    unit: Optional[str] = Field(default=None, max_length=100)
    test_method: Optional[str] = Field(default=None, max_length=400)
    criticality: str = "important"
    source: Optional[str] = Field(default=None, max_length=400)


class RequirementUpdateRequest(_Strict):
    property: Optional[str] = Field(default=None, max_length=200)
    threshold: Optional[float] = None
    direction: Optional[str] = None
    unit: Optional[str] = Field(default=None, max_length=100)
    test_method: Optional[str] = Field(default=None, max_length=400)
    criticality: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=400)


class RequirementResultRequest(_Strict):
    result_text: str = Field(min_length=1, max_length=1000)
    met: bool
    test_reference: Optional[str] = Field(default=None, max_length=300)


class MilestoneRequest(_Strict):
    title: str = Field(min_length=1, max_length=400)
    due_date: str


class MilestoneUpdateRequest(_Strict):
    title: Optional[str] = Field(default=None, max_length=400)
    due_date: Optional[str] = None
    completed: Optional[bool] = None


class TaskRequest(_Strict):
    title: str = Field(min_length=1, max_length=400)
    assignee_user_id: Optional[int] = None
    due_date: Optional[str] = None


class TaskUpdateRequest(_Strict):
    completed: bool


class CommentRequest(_Strict):
    kind: str = "comment"
    body: str = Field(min_length=1, max_length=8000)
