import json

from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def save_model_run(organization_id: int, project_id: int, run_type: str, result: dict) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO model_runs (organization_id, project_id, run_type, result_json, created_at)
                     VALUES (:org_id, :project_id, :run_type, :result_json, :created_at) RETURNING id"""),
            {"org_id": organization_id, "project_id": project_id, "run_type": run_type,
             "result_json": json.dumps(result), "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_latest_model_run(organization_id: int, project_id: int, run_type: str):
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT * FROM model_runs
                     WHERE organization_id = :org_id AND project_id = :project_id AND run_type = :run_type
                     ORDER BY id DESC LIMIT 1"""),
            {"org_id": organization_id, "project_id": project_id, "run_type": run_type},
        ).mappings().first()
        if not row:
            return None
        d = dict(row)
        d["result"] = json.loads(d.pop("result_json"))
        return d
