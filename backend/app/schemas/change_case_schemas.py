"""
Pydantic v2 schemas for the forced-substitution qualification diagnostic
(change_cases domain). Scope is deliberately narrow, per the approved
Phase 2 plan: customer-supplied candidates only (3-5), no candidate
discovery/generation, no open-ended formulation optimization.
"""
from typing import Literal
from pydantic import BaseModel, Field


TriggerType = Literal["regulatory_restriction", "supplier_loss", "component_obsolescence", "other"]
ChangeCaseStatus = Literal["draft", "active", "completed"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class CreateChangeCaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    trigger_type: TriggerType
    restricted_substance: str | None = None
    qualification_spec: dict = Field(default_factory=dict)


class UpdateStatusRequest(BaseModel):
    status: ChangeCaseStatus


class AddCandidateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    features: dict[str, float] = Field(
        description="Candidate's known property values, keyed by the same "
        "feature column names used in the uploaded qualification dataset."
    )


class RecordOutcomeRequest(BaseModel):
    recommended_experiment_id: int | None = None
    actual_result: str = Field(min_length=1)
    passed_spec: bool


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class ChangeCaseResponse(BaseModel):
    id: int
    name: str
    trigger_type: TriggerType
    restricted_substance: str | None
    status: ChangeCaseStatus
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class QualificationDatasetResponse(BaseModel):
    id: int
    original_filename: str
    row_count: int
    created_at: str

    model_config = {"from_attributes": True}


class PredictionResponse(BaseModel):
    predicted_value: float | None = None
    uncertainty_std: float
    predicted_probability: float
    model_version: str
    dataset_version_id: int

    model_config = {"protected_namespaces": ()}


class CandidateResponse(BaseModel):
    id: int
    candidate_name: str
    properties: dict
    latest_prediction: PredictionResponse | None = None


class RecommendedExperimentResponse(BaseModel):
    id: int
    description: str
    priority: int


class RankResultResponse(BaseModel):
    """Response for POST /rank -- every candidate's fresh prediction plus
    its recommended validation experiment, best-ranked first."""
    candidate_id: int
    candidate_name: str
    predicted_probability: float
    uncertainty_std: float
    recommended_experiment: str


class OutcomeResponse(BaseModel):
    id: int
    candidate_id: int
    actual_result: str
    passed_spec: bool
    created_at: str
