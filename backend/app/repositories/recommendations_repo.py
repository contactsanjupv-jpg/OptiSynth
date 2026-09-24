import json

from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def save_recommendations(organization_id: int, project_id: int, generated_by_user_id: int,
                          recommendations: list) -> list:
    """Persists engine output so the Candidate Details page can fetch one
    by id later. Returns the inserted rows' ids."""
    timestamp = now_iso()
    ids = []
    with db_transaction() as conn:
        for rank, rec in enumerate(recommendations, start=1):
            row = conn.execute(
                text("""INSERT INTO recommendations
                         (organization_id, project_id, rank, features_json, predicted_value,
                          uncertainty_std, feasibility_probability, acquisition_score,
                          generated_by_user_id, created_at)
                         VALUES (:org_id, :project_id, :rank, :features_json, :predicted_value,
                                 :uncertainty_std, :feasibility_probability, :acquisition_score,
                                 :generated_by, :created_at) RETURNING id"""),
                {"org_id": organization_id, "project_id": project_id, "rank": rank,
                 "features_json": json.dumps(rec["features"]), "predicted_value": rec["predicted_value"],
                 "uncertainty_std": rec["uncertainty_std"],
                 "feasibility_probability": rec["feasibility_probability"],
                 "acquisition_score": rec["acquisition_score"], "generated_by": generated_by_user_id,
                 "created_at": timestamp},
            ).mappings().first()
            ids.append(row["id"])
    return ids


def list_latest_recommendations(organization_id: int, project_id: int):
    with db_connection() as conn:
        latest = conn.execute(
            text("""SELECT created_at FROM recommendations
                     WHERE organization_id = :org_id AND project_id = :project_id
                     ORDER BY created_at DESC LIMIT 1"""),
            {"org_id": organization_id, "project_id": project_id},
        ).mappings().first()
        if not latest:
            return []
        rows = conn.execute(
            text("""SELECT * FROM recommendations
                     WHERE organization_id = :org_id AND project_id = :project_id AND created_at = :ts
                     ORDER BY rank ASC"""),
            {"org_id": organization_id, "project_id": project_id, "ts": latest["created_at"]},
        ).mappings().all()
        return [_deserialize(dict(r)) for r in rows]


def get_recommendation(organization_id: int, recommendation_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM recommendations WHERE id = :id AND organization_id = :org_id"),
            {"id": recommendation_id, "org_id": organization_id},
        ).mappings().first()
        return _deserialize(dict(row)) if row else None


def _deserialize(row: dict) -> dict:
    row["features"] = json.loads(row.pop("features_json"))
    return row
