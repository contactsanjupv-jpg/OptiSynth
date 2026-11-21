"""One row per recipient. Every query is scoped by the recipient's user_id and organization_id."""
from sqlalchemy import text

from backend.app.config.database import db_connection, db_transaction
from backend.app.repositories._time import now_iso


def admin_recipients(organization_id: int, exclude_user_id=None) -> list:
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT m.user_id FROM memberships m JOIN users u ON u.id = m.user_id
                     WHERE m.organization_id = :org AND m.role IN ('owner', 'admin') AND u.is_active = :yes"""),
            {"org": organization_id, "yes": 1}).all()
        return [r[0] for r in rows if r[0] != exclude_user_id]


def create_many(organization_id: int, user_ids: list, kind: str, title: str, body, link) -> int:
    if not user_ids:
        return 0
    now = now_iso()
    with db_transaction() as conn:
        for uid in user_ids:
            conn.execute(
                text("""INSERT INTO notifications (organization_id, user_id, kind, title, body, link, created_at)
                         VALUES (:org, :uid, :kind, :title, :body, :link, :now)"""),
                {"org": organization_id, "uid": uid, "kind": kind, "title": title, "body": body, "link": link, "now": now})
    return len(user_ids)


def list_for_user(organization_id: int, user_id: int, unread_only: bool, limit: int = 50) -> list:
    where = "organization_id = :org AND user_id = :u" + (" AND read_at IS NULL" if unread_only else "")
    with db_connection() as conn:
        rows = conn.execute(
            text(f"SELECT id, kind, title, body, link, created_at, read_at FROM notifications WHERE {where} ORDER BY id DESC LIMIT :limit"),
            {"org": organization_id, "u": user_id, "limit": limit}).mappings().all()
        return [dict(r) for r in rows]


def unread_count(organization_id: int, user_id: int) -> int:
    with db_connection() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM notifications WHERE organization_id = :org AND user_id = :u AND read_at IS NULL"),
            {"org": organization_id, "u": user_id}).scalar()


def mark_read(organization_id: int, user_id: int, notification_id: int) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE notifications SET read_at = :now
                     WHERE id = :id AND organization_id = :org AND user_id = :u AND read_at IS NULL"""),
            {"now": now_iso(), "id": notification_id, "org": organization_id, "u": user_id})
        return result.rowcount > 0


def mark_all_read(organization_id: int, user_id: int) -> int:
    with db_transaction() as conn:
        result = conn.execute(
            text("UPDATE notifications SET read_at = :now WHERE organization_id = :org AND user_id = :u AND read_at IS NULL"),
            {"now": now_iso(), "org": organization_id, "u": user_id})
        return result.rowcount
