"""Evidence documents, their review history and detected conflicts. Every query is scoped by organization_id."""
import json

from sqlalchemy import text

from backend.app.config.database import db_connection, db_transaction
from backend.app.repositories._time import now_iso

_DATASET_SELECT = """SELECT d.*, c.name AS program_name, u.display_name AS uploader_name, u.email AS uploader_email,
       r.display_name AS reviewer_name, r.email AS reviewer_email
  FROM qualification_datasets d JOIN change_cases c ON c.id = d.change_case_id
  LEFT JOIN users u ON u.id = d.uploaded_by_user_id LEFT JOIN users r ON r.id = d.reviewed_by_user_id"""


def _all(conn, sql, params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def list_for_case(organization_id: int, case_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, _DATASET_SELECT + " WHERE d.organization_id = :org AND d.change_case_id = :c ORDER BY d.id",
                    {"org": organization_id, "c": case_id})


def get_dataset(organization_id: int, case_id: int, dataset_id: int):
    with db_connection() as conn:
        rows = _all(conn, _DATASET_SELECT + " WHERE d.id = :id AND d.organization_id = :org AND d.change_case_id = :c",
                    {"id": dataset_id, "org": organization_id, "c": case_id})
        return rows[0] if rows else None


def library(organization_id: int, q=None, document_type=None, status=None, program_id=None, limit: int = 50, offset: int = 0) -> tuple:
    where, params = ["d.organization_id = :org"], {"org": organization_id, "limit": limit, "offset": offset}
    if q:
        where.append("""(LOWER(d.original_filename) LIKE :q OR LOWER(COALESCE(d.source_note, '')) LIKE :q OR LOWER(COALESCE(d.document_version, '')) LIKE :q
                         OR LOWER(COALESCE(d.test_method, '')) LIKE :q OR LOWER(c.name) LIKE :q)""")
        params["q"] = f"%{q.lower()}%"
    if document_type:
        where.append("d.document_type = :dt"); params["dt"] = document_type
    if status:
        where.append("d.review_status = :st"); params["st"] = status
    if program_id:
        where.append("d.change_case_id = :pid"); params["pid"] = program_id
    clause = " AND ".join(where)
    with db_connection() as conn:
        total = conn.execute(text(f"SELECT COUNT(*) FROM qualification_datasets d JOIN change_cases c ON c.id = d.change_case_id WHERE {clause}"),
                             params).scalar()
        rows = _all(conn, _DATASET_SELECT + f" WHERE {clause} ORDER BY d.id DESC LIMIT :limit OFFSET :offset", params)
    return total, rows


def update_metadata(organization_id: int, dataset_id: int, fields: dict) -> None:
    if not fields or set(fields) - {"document_type", "source_note", "document_version", "test_method", "document_date", "source_reliability"}:
        raise ValueError("Not editable")
    assignments = ", ".join(f"{c} = :{c}" for c in fields)
    with db_transaction() as conn:
        conn.execute(text(f"UPDATE qualification_datasets SET {assignments} WHERE id = :id AND organization_id = :org"),
                     {**fields, "id": dataset_id, "org": organization_id})


def set_review(organization_id: int, case_id: int, dataset_id: int, action: str, from_status: str, to_status: str, reason,
               related_id, actor_id: int) -> bool:
    """Changes the status and writes the history row in one transaction. Applies only if the status is still `from_status`."""
    now = now_iso()
    with db_transaction() as conn:
        done = conn.execute(
            text("""UPDATE qualification_datasets SET review_status = :to, review_reason = :reason, reviewed_by_user_id = :actor,
                           reviewed_at = :now, superseded_by_dataset_id = :related
                     WHERE id = :id AND organization_id = :org AND change_case_id = :c AND review_status = :frm"""),
            {"to": to_status, "reason": reason, "actor": actor_id, "now": now, "related": related_id if action == "supersede" else None,
             "id": dataset_id, "org": organization_id, "c": case_id, "frm": from_status}).rowcount
        if not done:
            return False
        conn.execute(
            text("""INSERT INTO evidence_reviews (organization_id, change_case_id, dataset_id, action, from_status, to_status, reason,
                                                  related_dataset_id, actor_user_id, created_at)
                     VALUES (:org, :c, :id, :action, :frm, :to, :reason, :related, :actor, :now)"""),
            {"org": organization_id, "c": case_id, "id": dataset_id, "action": action, "frm": from_status, "to": to_status,
             "reason": reason, "related": related_id, "actor": actor_id, "now": now})
        return True


def review_history(organization_id: int, dataset_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT h.id, h.action, h.from_status, h.to_status, h.reason, h.related_dataset_id, h.created_at,
                                    u.display_name AS actor_name, u.email AS actor_email
                               FROM evidence_reviews h LEFT JOIN users u ON u.id = h.actor_user_id
                              WHERE h.organization_id = :org AND h.dataset_id = :id ORDER BY h.id DESC""",
                    {"org": organization_id, "id": dataset_id})


def rows_for_dataset(organization_id: int, dataset_id: int, offset: int = 0, limit: int = None) -> list:
    sql = "SELECT * FROM qualification_experiments WHERE organization_id = :org AND qualification_dataset_id = :id ORDER BY id"
    params = {"org": organization_id, "id": dataset_id}
    if limit is not None:
        sql += " LIMIT :limit OFFSET :offset"
        params.update(limit=limit, offset=offset)
    with db_connection() as conn:
        return _all(conn, sql, params)


def newest_held_after(organization_id: int, case_id: int, dataset_id: int):
    """A dataset newer than `dataset_id` that is under review (clarification requested or conflict flagged), or None."""
    with db_connection() as conn:
        rows = _all(conn, """SELECT id, original_filename, review_status FROM qualification_datasets
                              WHERE organization_id = :org AND change_case_id = :c AND id > :id
                                AND review_status IN ('clarification_requested', 'conflict_flagged') ORDER BY id DESC LIMIT 1""",
                    {"org": organization_id, "c": case_id, "id": dataset_id or 0})
        return rows[0] if rows else None


# ---------------------------------------------------------------- conflicts
def list_conflicts(organization_id: int, case_id: int, status=None) -> list:
    where = " AND status = :st" if status else ""
    with db_connection() as conn:
        rows = _all(conn, f"""SELECT x.*, u.display_name AS resolver_name, u.email AS resolver_email FROM evidence_conflicts x
                               LEFT JOIN users u ON u.id = x.resolved_by_user_id
                              WHERE x.organization_id = :org AND x.change_case_id = :c{where} ORDER BY x.id""",
                    {"org": organization_id, "c": case_id, "st": status})
    for r in rows:
        r["dataset_ids"] = json.loads(r.pop("dataset_ids_json"))
        r["details"] = json.loads(r.pop("details_json") or "{}")
    return rows


def get_conflict(organization_id: int, case_id: int, conflict_id: int):
    return next((c for c in list_conflicts(organization_id, case_id) if c["id"] == conflict_id), None)


def sync_conflicts(organization_id: int, case_id: int, detected: list) -> dict:
    """Stores what was detected. An open conflict that is no longer detected is closed by the system. A conflict a person
    resolved keeps their decision even if it is detected again."""
    now = now_iso()
    added = closed = 0
    with db_transaction() as conn:
        existing = {r["fingerprint"]: dict(r) for r in conn.execute(
            text("SELECT id, fingerprint, status FROM evidence_conflicts WHERE organization_id = :org AND change_case_id = :c"),
            {"org": organization_id, "c": case_id}).mappings()}
        seen = set()
        for d in detected:
            seen.add(d["fingerprint"])
            params = {"org": organization_id, "c": case_id, "kind": d["kind"], "sev": d["severity"], "title": d["title"],
                      "desc": d["description"], "ids": json.dumps(d["dataset_ids"]), "details": json.dumps(d["details"]),
                      "fp": d["fingerprint"], "now": now}
            if d["fingerprint"] in existing:
                if existing[d["fingerprint"]]["status"] == "open":
                    conn.execute(text("""UPDATE evidence_conflicts SET description = :desc, details_json = :details, severity = :sev
                                          WHERE organization_id = :org AND change_case_id = :c AND fingerprint = :fp"""), params)
                continue
            conn.execute(text("""INSERT INTO evidence_conflicts (organization_id, change_case_id, kind, severity, title, description,
                                     dataset_ids_json, details_json, fingerprint, status, detected_at)
                                  VALUES (:org, :c, :kind, :sev, :title, :desc, :ids, :details, :fp, 'open', :now)"""), params)
            added += 1
        for fp, row in existing.items():
            if fp not in seen and row["status"] == "open":
                conn.execute(text("""UPDATE evidence_conflicts SET status = 'resolved', resolution = 'no_longer_detected',
                                         resolution_note = 'No longer detected after the evidence changed.', resolved_at = :now
                                      WHERE id = :id AND organization_id = :org"""), {"now": now, "id": row["id"], "org": organization_id})
                closed += 1
    return {"added": added, "closed": closed}


def resolve_conflict(organization_id: int, conflict_id: int, status: str, resolution: str, note: str, preferred_id, user_id: int) -> bool:
    with db_transaction() as conn:
        return conn.execute(
            text("""UPDATE evidence_conflicts SET status = :st, resolution = :res, resolution_note = :note, preferred_dataset_id = :pref,
                           resolved_by_user_id = :u, resolved_at = :now
                     WHERE id = :id AND organization_id = :org AND status = 'open'"""),
            {"st": status, "res": resolution, "note": note, "pref": preferred_id, "u": user_id, "now": now_iso(),
             "id": conflict_id, "org": organization_id}).rowcount > 0


def open_conflict_dataset_counts(organization_id: int) -> dict:
    """{dataset_id: number of open conflicts that involve it}"""
    counts = {}
    with db_connection() as conn:
        for r in conn.execute(text("SELECT dataset_ids_json FROM evidence_conflicts WHERE organization_id = :org AND status = 'open'"),
                              {"org": organization_id}):
            for i in json.loads(r[0]):
                counts[i] = counts.get(i, 0) + 1
    return counts
