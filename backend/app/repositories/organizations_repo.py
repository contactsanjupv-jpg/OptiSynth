"""
Every INSERT in this repository layer uses `RETURNING id` rather than a
driver-specific lastrowid -- SQLite 3.35+ and PostgreSQL both support
RETURNING, so this one pattern works unchanged against either backend
(unlike relying on cursor.lastrowid, which psycopg2 does not populate).
"""
from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_organization(name: str) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("INSERT INTO organizations (name, created_at) VALUES (:name, :created_at) RETURNING id"),
            {"name": name, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_organization(organization_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM organizations WHERE id = :id"), {"id": organization_id}
        ).mappings().first()
        return dict(row) if row else None
