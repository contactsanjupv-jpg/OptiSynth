from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_dataset(organization_id: int, project_id: int, uploaded_by_user_id: int,
                    original_filename: str, stored_filename: str, row_count: int) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO datasets
                     (organization_id, project_id, uploaded_by_user_id, original_filename,
                      stored_filename, row_count, created_at)
                     VALUES (:org_id, :project_id, :uploaded_by, :original_filename,
                             :stored_filename, :row_count, :created_at) RETURNING id"""),
            {"org_id": organization_id, "project_id": project_id, "uploaded_by": uploaded_by_user_id,
             "original_filename": original_filename, "stored_filename": stored_filename,
             "row_count": row_count, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_datasets_for_project(organization_id: int, project_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM datasets WHERE organization_id = :org_id AND project_id = :project_id
                     ORDER BY id DESC"""),
            {"org_id": organization_id, "project_id": project_id},
        ).mappings().all()
        return [dict(r) for r in rows]
