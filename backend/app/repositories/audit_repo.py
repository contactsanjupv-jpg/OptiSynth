from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def write_audit_log(organization_id, user_id, action: str, project_id=None, detail: str = None) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO audit_logs (organization_id, user_id, project_id, action, detail, created_at)
                     VALUES (:org_id, :user_id, :project_id, :action, :detail, :created_at) RETURNING id"""),
            {"org_id": organization_id, "user_id": user_id, "project_id": project_id,
             "action": action, "detail": detail, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_audit_logs(organization_id: int, project_id: int = None, limit: int = 200):
    with db_connection() as conn:
        if project_id is not None:
            rows = conn.execute(
                text("""SELECT * FROM audit_logs WHERE organization_id = :org_id AND project_id = :project_id
                         ORDER BY id DESC LIMIT :limit"""),
                {"org_id": organization_id, "project_id": project_id, "limit": limit},
            ).mappings().all()
        else:
            rows = conn.execute(
                text("SELECT * FROM audit_logs WHERE organization_id = :org_id ORDER BY id DESC LIMIT :limit"),
                {"org_id": organization_id, "limit": limit},
            ).mappings().all()
        return [dict(r) for r in rows]
