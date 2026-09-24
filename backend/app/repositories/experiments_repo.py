import json

from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def bulk_create_experiments(organization_id: int, project_id: int, dataset_id: int, rows: list) -> int:
    """rows: list of {features: dict, target_value: float, constraint_values: dict}."""
    timestamp = now_iso()
    with db_transaction() as conn:
        for r in rows:
            conn.execute(
                text("""INSERT INTO experiments
                         (organization_id, project_id, dataset_id, features_json, target_value,
                          constraint_values_json, source, created_at)
                         VALUES (:org_id, :project_id, :dataset_id, :features_json, :target_value,
                                 :constraint_values_json, 'historical', :created_at)"""),
                {"org_id": organization_id, "project_id": project_id, "dataset_id": dataset_id,
                 "features_json": json.dumps(r["features"]), "target_value": r["target_value"],
                 "constraint_values_json": json.dumps(r.get("constraint_values", {})),
                 "created_at": timestamp},
            )
    return len(rows)


def list_experiments(organization_id: int, project_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM experiments WHERE organization_id = :org_id AND project_id = :project_id
                     ORDER BY id ASC"""),
            {"org_id": organization_id, "project_id": project_id},
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            d["constraint_values"] = json.loads(d.pop("constraint_values_json") or "{}")
            out.append(d)
        return out
