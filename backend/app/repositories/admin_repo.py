"""Read only queries for audit history, security events and data inventory. Scoped by organization_id."""
from sqlalchemy import text

from backend.app.config.database import db_connection

# Tables copied into a data export. Only tables with an organization_id column are listed, and columns that are secrets or
# server internals are dropped.
EXPORT_TABLES = ("change_cases", "candidate_substitutes", "predictions", "recommended_experiments", "qualification_outcomes",
                 "qualification_experiments", "qualification_datasets", "program_stage_history", "invitations", "audit_logs")
EXPORT_DROP_COLUMNS = {"token", "password_hash", "stored_filename", "stored_path", "session_id"}


def audit_events(organization_id: int, user_id=None, action=None, q=None, date_from=None, date_to=None,
                 limit: int = 50, offset: int = 0) -> tuple:
    where, params = ["a.organization_id = :org"], {"org": organization_id, "limit": limit, "offset": offset}
    if user_id:
        where.append("a.user_id = :uid"); params["uid"] = user_id
    if action:
        where.append("a.action = :action"); params["action"] = action
    if q:
        where.append("(LOWER(COALESCE(a.detail, '')) LIKE :q OR LOWER(a.action) LIKE :q)"); params["q"] = f"%{q.lower()}%"
    if date_from:
        where.append("a.created_at >= :dfrom"); params["dfrom"] = date_from
    if date_to:
        where.append("a.created_at < :dto"); params["dto"] = date_to
    clause = " AND ".join(where)
    with db_connection() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM audit_logs a WHERE {clause}"), params).scalar()
        rows = conn.execute(
            text(f"""SELECT a.id, a.action, a.detail, a.created_at, a.user_id, u.display_name AS actor_name, u.email AS actor_email
                       FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
                      WHERE {clause} ORDER BY a.id DESC LIMIT :limit OFFSET :offset"""), params).mappings().all()
        return total, [dict(r) for r in rows]


def audit_actions(organization_id: int) -> list:
    with db_connection() as conn:
        return [r[0] for r in conn.execute(
            text("SELECT DISTINCT action FROM audit_logs WHERE organization_id = :org ORDER BY action"), {"org": organization_id})]


def security_events(organization_id: int, user_id: int, limit: int = 30) -> list:
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT id, action, detail, created_at FROM audit_logs
                     WHERE organization_id = :org AND user_id = :uid AND action LIKE 'auth.%'
                     ORDER BY id DESC LIMIT :limit"""), {"org": organization_id, "uid": user_id, "limit": limit}).mappings().all()
        return [dict(r) for r in rows]


def upload_inventory(organization_id: int) -> list:
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT d.id, d.change_case_id, c.name AS program_name, d.original_filename, d.stored_filename, d.row_count,
                           d.created_at, u.display_name AS uploader_name, u.email AS uploader_email
                      FROM qualification_datasets d JOIN change_cases c ON c.id = d.change_case_id
                      LEFT JOIN users u ON u.id = d.uploaded_by_user_id
                     WHERE d.organization_id = :org ORDER BY d.id DESC"""), {"org": organization_id}).mappings().all()
        return [dict(r) for r in rows]


def export_table(organization_id: int, table: str) -> list:
    if table not in EXPORT_TABLES:
        raise ValueError("table not exportable")
    with db_connection() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table} WHERE organization_id = :org ORDER BY id"), {"org": organization_id}).mappings().all()
        return [{k: v for k, v in dict(r).items() if k not in EXPORT_DROP_COLUMNS} for r in rows]


def export_members(organization_id: int) -> list:
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT u.id, u.email, u.display_name, m.role, m.created_at FROM memberships m JOIN users u ON u.id = m.user_id
                     WHERE m.organization_id = :org ORDER BY u.id"""), {"org": organization_id}).mappings().all()
        return [dict(r) for r in rows]
