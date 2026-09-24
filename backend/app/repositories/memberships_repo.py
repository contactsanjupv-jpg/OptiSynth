from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_membership(user_id: int, organization_id: int, role: str = "owner") -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO memberships (user_id, organization_id, role, created_at)
                     VALUES (:user_id, :organization_id, :role, :created_at) RETURNING id"""),
            {"user_id": user_id, "organization_id": organization_id, "role": role, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_membership(user_id: int, organization_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM memberships WHERE user_id = :user_id AND organization_id = :org_id"),
            {"user_id": user_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None


def list_memberships_for_user(user_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT m.*, o.name AS organization_name FROM memberships m
                     JOIN organizations o ON o.id = m.organization_id
                     WHERE m.user_id = :user_id"""),
            {"user_id": user_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def list_members_for_organization(organization_id: int):
    """Members with their user info joined in -- what the Team page shows."""
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT m.id AS membership_id, m.role, m.created_at AS member_since,
                            u.id AS user_id, u.email, u.display_name, u.is_active
                     FROM memberships m
                     JOIN users u ON u.id = m.user_id
                     WHERE m.organization_id = :org_id
                     ORDER BY m.created_at ASC"""),
            {"org_id": organization_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def count_owners(organization_id: int) -> int:
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS n FROM memberships WHERE organization_id = :org_id AND role = 'owner'"),
            {"org_id": organization_id},
        ).mappings().first()
        return row["n"]


def remove_membership(organization_id: int, user_id: int) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("DELETE FROM memberships WHERE organization_id = :org_id AND user_id = :user_id"),
            {"org_id": organization_id, "user_id": user_id},
        )
        return result.rowcount > 0


def update_membership_role(organization_id: int, user_id: int, role: str) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE memberships SET role = :role
                     WHERE organization_id = :org_id AND user_id = :user_id"""),
            {"role": role, "org_id": organization_id, "user_id": user_id},
        )
        return result.rowcount > 0