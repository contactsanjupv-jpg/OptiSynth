from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_change_case(organization_id: int, created_by_user_id: int, name: str,
                        trigger_type: str, restricted_substance: str,
                        qualification_spec_json: str) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO change_cases
                     (organization_id, created_by_user_id, name, trigger_type,
                      restricted_substance, qualification_spec_json, status,
                      created_at, updated_at)
                     VALUES (:org_id, :created_by, :name, :trigger_type,
                             :restricted_substance, :spec_json, 'draft',
                             :created_at, :updated_at) RETURNING id"""),
            {"org_id": organization_id, "created_by": created_by_user_id, "name": name,
             "trigger_type": trigger_type, "restricted_substance": restricted_substance,
             "spec_json": qualification_spec_json, "created_at": now_iso(), "updated_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_change_case(organization_id: int, change_case_id: int):
    """Org-scoped lookup -- returns None if the change case doesn't exist
    OR belongs to a different organization, so callers get the same 404
    behavior for both cases (never distinguishing 'not found' from
    'not yours' to the client, matching project_routes.py's existing
    pattern)."""
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM change_cases WHERE id = :id AND organization_id = :org_id"),
            {"id": change_case_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None


def list_change_cases(organization_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM change_cases WHERE organization_id = :org_id ORDER BY id DESC"),
            {"org_id": organization_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def update_status(organization_id: int, change_case_id: int, new_status: str) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE change_cases SET status = :status, updated_at = :updated_at
                     WHERE id = :id AND organization_id = :org_id"""),
            {"status": new_status, "updated_at": now_iso(), "id": change_case_id, "org_id": organization_id},
        )
        return result.rowcount > 0
