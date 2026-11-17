"""Server-side record of every issued session (see security/sessions.py).

A signed cookie alone cannot be taken back: a copy stays valid until it expires. Each login/signup therefore
also stores one row here, the signed cookie carries that row's random session_id, and every request is accepted
only while the row exists, is not revoked and has not expired. Logout sets revoked_at on that one row, so other
sessions (of the same user or anyone else) are untouched. State lives in the database, so it survives restarts and
is shared by every worker process."""
from datetime import datetime, timezone

from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_session(session_id: str, user_id: int, organization_id: int, expires_at: str) -> None:
    with db_transaction() as conn:
        # Rows past their expiry can never be valid again; dropping them here keeps the table small without a scheduler.
        conn.execute(text("DELETE FROM user_sessions WHERE expires_at < :now"), {"now": now_iso()})
        conn.execute(
            text("""INSERT INTO user_sessions (session_id, user_id, organization_id, created_at, expires_at)
                     VALUES (:sid, :user_id, :org_id, :created_at, :expires_at)"""),
            {"sid": session_id, "user_id": user_id, "org_id": organization_id,
             "created_at": now_iso(), "expires_at": expires_at},
        )


def is_session_active(session_id: str, user_id: int, organization_id: int) -> bool:
    """True only for an existing, unrevoked, unexpired session that belongs to exactly this user and organization."""
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT user_id, organization_id, expires_at, revoked_at FROM user_sessions
                     WHERE session_id = :sid"""), {"sid": session_id},
        ).mappings().first()
    if not row or row["revoked_at"] is not None:
        return False
    if row["user_id"] != user_id or row["organization_id"] != organization_id:
        return False
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except (TypeError, ValueError):
        return False  # an unreadable expiry is treated as expired: fail closed
    return expires > datetime.now(timezone.utc)


def revoke_session(session_id: str) -> bool:
    """Marks one session revoked. Returns False if it was unknown or already revoked."""
    with db_transaction() as conn:
        result = conn.execute(
            text("UPDATE user_sessions SET revoked_at = :now WHERE session_id = :sid AND revoked_at IS NULL"),
            {"now": now_iso(), "sid": session_id},
        )
        return result.rowcount > 0
