import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso

INVITE_EXPIRY_DAYS = 14


def generate_invite_token() -> str:
    return secrets.token_urlsafe(24)


def create_invitation(organization_id: int, email: str, role: str, invited_by_user_id: int) -> dict:
    token = generate_invite_token()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)).isoformat()
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO invitations
                     (organization_id, email, role, token, invited_by_user_id, status, created_at, expires_at)
                     VALUES (:org_id, :email, :role, :token, :invited_by, 'pending', :created_at, :expires_at)
                     RETURNING id"""),
            {"org_id": organization_id, "email": email.lower().strip(), "role": role, "token": token,
             "invited_by": invited_by_user_id, "created_at": now_iso(), "expires_at": expires_at},
        ).mappings().first()
        return {"id": row["id"], "token": token, "expires_at": expires_at}


def get_invitation_by_token(token: str):
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT i.*, o.name AS organization_name FROM invitations i
                     JOIN organizations o ON o.id = i.organization_id
                     WHERE i.token = :token"""),
            {"token": token},
        ).mappings().first()
        return dict(row) if row else None


def list_pending_invitations(organization_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM invitations
                     WHERE organization_id = :org_id AND status = 'pending'
                     ORDER BY created_at DESC"""),
            {"org_id": organization_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def mark_invitation_accepted(invitation_id: int) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("UPDATE invitations SET status = 'accepted' WHERE id = :id AND status = 'pending'"),
            {"id": invitation_id},
        )
        return result.rowcount > 0


def revoke_invitation(organization_id: int, invitation_id: int) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE invitations SET status = 'revoked'
                     WHERE id = :id AND organization_id = :org_id AND status = 'pending'"""),
            {"id": invitation_id, "org_id": organization_id},
        )
        return result.rowcount > 0