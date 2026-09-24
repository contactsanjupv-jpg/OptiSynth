from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_user(email: str, password_hash: str, display_name: str = None) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO users (email, password_hash, display_name, is_active, created_at)
                     VALUES (:email, :password_hash, :display_name, 1, :created_at) RETURNING id"""),
            {"email": email.lower().strip(), "password_hash": password_hash,
             "display_name": display_name, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_user_by_email(email: str):
    """Includes password_hash -- caller (auth_service) verifies and then
    NEVER passes the hash back out to the API layer."""
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM users WHERE email = :email"), {"email": email.lower().strip()}
        ).mappings().first()
        return dict(row) if row else None


def get_user_public(user_id: int):
    """Safe-to-return-to-client projection -- no password_hash."""
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT id, email, display_name, is_active, created_at FROM users WHERE id = :id"),
            {"id": user_id},
        ).mappings().first()
        return dict(row) if row else None


def user_is_active(user_id: int) -> bool:
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT is_active FROM users WHERE id = :id"), {"id": user_id}
        ).mappings().first()
        return bool(row and row["is_active"])
