"""
Every function here takes organization_id as an explicit, required
parameter and includes it in the WHERE clause of every query -- this is
the server-side tenant-isolation enforcement point for projects. Callers
(services/project_service.py) get organization_id from the authenticated
session, never from a client-supplied field.
"""
import json

from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_project(organization_id: int, created_by_user_id: int, name: str, objective: str,
                    target_metric: str, direction: str, feature_columns: list,
                    constraints: list, target_value=None) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO projects
                     (organization_id, created_by_user_id, name, objective, target_metric, direction,
                      feature_columns_json, constraints_json, target_value, status, created_at, updated_at)
                     VALUES (:org_id, :created_by, :name, :objective, :target_metric, :direction,
                             :feature_columns_json, :constraints_json, :target_value, 'draft', :created_at, :updated_at)
                     RETURNING id"""),
            {"org_id": organization_id, "created_by": created_by_user_id, "name": name,
             "objective": objective, "target_metric": target_metric, "direction": direction,
             "feature_columns_json": json.dumps(feature_columns),
             "constraints_json": json.dumps(constraints or []),
             "target_value": target_value, "created_at": now_iso(), "updated_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_projects(organization_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM projects WHERE organization_id = :org_id ORDER BY id DESC"),
            {"org_id": organization_id},
        ).mappings().all()
        return [_deserialize(dict(r)) for r in rows]


def get_project(organization_id: int, project_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM projects WHERE id = :id AND organization_id = :org_id"),
            {"id": project_id, "org_id": organization_id},
        ).mappings().first()
        return _deserialize(dict(row)) if row else None


def update_project_status(organization_id: int, project_id: int, status: str) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE projects SET status = :status, updated_at = :updated_at
                     WHERE id = :id AND organization_id = :org_id"""),
            {"status": status, "updated_at": now_iso(), "id": project_id, "org_id": organization_id},
        )
        return result.rowcount > 0


def update_project(organization_id: int, project_id: int, fields: dict) -> bool:
    """Generic partial update. `fields` may contain any of: name, objective,
    target_metric, direction, target_value, feature_columns, constraints --
    only the keys actually present are updated (see
    services/project_service.py#update_project for WHY feature_columns and
    constraints are only ever included here when the project has zero
    experiments -- that check happens one layer up, not here, but this
    function will happily overwrite them if asked, so don't call it
    directly from a route)."""
    if not fields:
        return False

    set_clauses = []
    params = {"id": project_id, "org_id": organization_id, "updated_at": now_iso()}
    for key in ("name", "objective", "target_metric", "direction", "target_value"):
        if key in fields:
            set_clauses.append(f"{key} = :{key}")
            params[key] = fields[key]
    if "feature_columns" in fields:
        set_clauses.append("feature_columns_json = :feature_columns_json")
        params["feature_columns_json"] = json.dumps(fields["feature_columns"])
    if "constraints" in fields:
        set_clauses.append("constraints_json = :constraints_json")
        params["constraints_json"] = json.dumps(fields["constraints"])

    if not set_clauses:
        return False

    set_clauses.append("updated_at = :updated_at")
    with db_transaction() as conn:
        result = conn.execute(
            text(f"UPDATE projects SET {', '.join(set_clauses)} WHERE id = :id AND organization_id = :org_id"),
            params,
        )
        return result.rowcount > 0


def _deserialize(row: dict) -> dict:
    row["feature_columns"] = json.loads(row.pop("feature_columns_json") or "[]")
    row["constraints"] = json.loads(row.pop("constraints_json") or "[]")
    return row