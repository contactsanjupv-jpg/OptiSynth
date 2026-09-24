"""
Runtime-tested as part of the full FastAPI application (backend/tests/test_api.py)
and via real manual use (project creation through candidate recommendation,
backtest, and report download) on the developer's own machine.
"""
from fastapi import APIRouter, Depends, Query

from backend.app.schemas.optimization_schemas import RecommendRequest
from backend.app.services import project_service, optimization_service, audit_service
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/projects", tags=["optimization"])
candidates_router = APIRouter(prefix="/api/candidates", tags=["optimization"])


@router.post("/{project_id}/recommend")
def recommend(project_id: int, body: RecommendRequest, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.get_project_or_404(actor.organization_id, project_id)
    recs = optimization_service.get_recommendations(
        actor.organization_id, actor.user["id"], project, body.n_recommendations
    )
    audit_service.log(actor.organization_id, actor.user["id"], "optimization.recommend", project_id)
    return {"recommendations": recs}


@router.get("/{project_id}/candidates")
def latest_candidates(project_id: int, actor: AuthContext = Depends(get_current_actor)):
    project_service.get_project_or_404(actor.organization_id, project_id)
    return optimization_service.get_latest_recommendations(actor.organization_id, project_id)


@router.get("/{project_id}/backtest")
def backtest(project_id: int, target: float | None = Query(default=None),
             actor: AuthContext = Depends(get_current_actor)):
    project = project_service.get_project_or_404(actor.organization_id, project_id)
    result = optimization_service.run_backtest_for_project(actor.organization_id, project, target)
    audit_service.log(actor.organization_id, actor.user["id"], "optimization.backtest", project_id)
    return result


@router.get("/{project_id}/model-metrics")
def model_metrics(project_id: int, actor: AuthContext = Depends(get_current_actor)):
    project = project_service.get_project_or_404(actor.organization_id, project_id)
    return optimization_service.get_model_metrics_for_project(actor.organization_id, project)


@candidates_router.get("/{recommendation_id}")
def candidate_detail(recommendation_id: int, actor: AuthContext = Depends(get_current_actor)):
    return optimization_service.get_recommendation_detail(actor.organization_id, recommendation_id)
