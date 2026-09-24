from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_report(organization_id: int, project_id: int, generated_by_user_id: int,
                   file_path: str, n_historical_rows: int, kind: str = "optimization_summary") -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO reports
                     (organization_id, project_id, generated_by_user_id, kind, file_path,
                      n_historical_rows, created_at)
                     VALUES (:org_id, :project_id, :generated_by, :kind, :file_path,
                             :n_rows, :created_at) RETURNING id"""),
            {"org_id": organization_id, "project_id": project_id, "generated_by": generated_by_user_id,
             "kind": kind, "file_path": file_path, "n_rows": n_historical_rows, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_reports(organization_id: int, project_id: int = None):
    with db_connection() as conn:
        if project_id is not None:
            rows = conn.execute(
                text("""SELECT * FROM reports WHERE organization_id = :org_id AND project_id = :project_id
                         ORDER BY id DESC"""),
                {"org_id": organization_id, "project_id": project_id},
            ).mappings().all()
        else:
            rows = conn.execute(
                text("SELECT * FROM reports WHERE organization_id = :org_id ORDER BY id DESC"),
                {"org_id": organization_id},
            ).mappings().all()
        return [dict(r) for r in rows]


def get_report(organization_id: int, report_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM reports WHERE id = :id AND organization_id = :org_id"),
            {"id": report_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None
