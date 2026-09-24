"""
NOT RUNTIME-TESTED IN THE SANDBOX: pydantic is not installed here. Syntax-
validated via py_compile only -- see README "Sandbox limitations".
"""
from pydantic import BaseModel, Field


class RecommendRequest(BaseModel):
    n_recommendations: int = Field(default=5, ge=1, le=20)


class RecommendationResponse(BaseModel):
    id: int | None = None
    project_id: int | None = None
    rank: int | None = None
    features: dict[str, float]
    predicted_value: float
    uncertainty_std: float
    feasibility_probability: float
    acquisition_score: float
    created_at: str | None = None
