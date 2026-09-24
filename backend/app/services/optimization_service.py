"""
Bridges the web layer and the optimization engine. This is the ONLY
service that imports from `engine` -- routes never call the engine
directly, and the engine never imports anything from `backend`.
"""
import numpy as np

from engine.facade import recommend_next, run_backtest, compute_model_metrics
from backend.app.repositories import experiments_repo, recommendations_repo, model_runs_repo
from backend.app.schemas.errors import ValidationError


def _load_xy(organization_id: int, project: dict):
    experiments = experiments_repo.list_experiments(organization_id, project["id"])
    if len(experiments) < 3:
        raise ValidationError(
            "Not enough historical data yet. Upload at least a few experiments before running optimization."
        )
    feature_columns = project["feature_columns"]
    X = np.array([[e["features"][c] for c in feature_columns] for e in experiments])
    y = np.array([e["target_value"] for e in experiments])
    constraint_data = {}
    for c in project["constraints"]:
        col = c["column"]
        constraint_data[col] = [e["constraint_values"].get(col) for e in experiments]
    return X, y, constraint_data, len(experiments)


def get_recommendations(organization_id: int, user_id: int, project: dict, n_recommend: int = 5) -> list:
    X, y, constraint_data, _ = _load_xy(organization_id, project)
    recs = recommend_next(
        X, y, project["direction"], project["feature_columns"],
        constraints=project["constraints"], constraint_data=constraint_data,
        n_recommend=n_recommend,
    )
    recommendations_repo.save_recommendations(organization_id, project["id"], user_id, recs)
    return recs


def get_latest_recommendations(organization_id: int, project_id: int) -> list:
    return recommendations_repo.list_latest_recommendations(organization_id, project_id)


def get_recommendation_detail(organization_id: int, recommendation_id: int) -> dict:
    rec = recommendations_repo.get_recommendation(organization_id, recommendation_id)
    if not rec:
        raise LookupError("Candidate not found.")
    return rec


def run_backtest_for_project(organization_id: int, project: dict, target: float = None) -> dict:
    X, y, constraint_data, n = _load_xy(organization_id, project)
    result = run_backtest(
        X, y, project["direction"],
        constraints=project["constraints"], constraint_data=constraint_data,
        target=target if target is not None else project.get("target_value"),
    )
    if "error" not in result:
        model_runs_repo.save_model_run(organization_id, project["id"], "backtest", result)
    return result


def get_model_metrics_for_project(organization_id: int, project: dict) -> dict:
    X, y, _, _ = _load_xy(organization_id, project)
    result = compute_model_metrics(X, y)
    if "error" not in result:
        model_runs_repo.save_model_run(organization_id, project["id"], "model_metrics", result)
    return result
