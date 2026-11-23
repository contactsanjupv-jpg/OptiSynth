"""Requirements, reviewers, milestones, tasks and comments of a program. Every query is scoped by organization_id."""
import re

from sqlalchemy import text

from backend.app.config.database import db_connection, db_transaction
from backend.app.repositories._time import now_iso

_DETAIL_COLUMNS = ("material_replaced", "reason_for_substitution", "business_impact", "original_formulation",
                   "manufacturing_constraints", "target_date")


def _one(conn, sql, params):
    row = conn.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def _all(conn, sql, params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


# ---------------------------------------------------------------- details and owner
def update_details(organization_id: int, case_id: int, fields: dict) -> None:
    unknown = set(fields) - set(_DETAIL_COLUMNS)
    if unknown or not fields:
        raise ValueError("Not editable")
    assignments = ", ".join(f"{c} = :{c}" for c in fields)
    with db_transaction() as conn:
        conn.execute(text(f"UPDATE change_cases SET {assignments}, updated_at = :now WHERE id = :id AND organization_id = :org"),
                     {**fields, "now": now_iso(), "id": case_id, "org": organization_id})


def set_owner(organization_id: int, case_id: int, user_id) -> None:
    with db_transaction() as conn:
        conn.execute(text("UPDATE change_cases SET owner_user_id = :u, updated_at = :now WHERE id = :id AND organization_id = :org"),
                     {"u": user_id, "now": now_iso(), "id": case_id, "org": organization_id})


def is_active_member(organization_id: int, user_id: int) -> bool:
    with db_connection() as conn:
        return conn.execute(
            text("""SELECT 1 FROM memberships m JOIN users u ON u.id = m.user_id
                     WHERE m.organization_id = :org AND m.user_id = :u AND u.is_active = :yes"""),
            {"org": organization_id, "u": user_id, "yes": 1}).first() is not None


def people(organization_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT u.id, u.display_name, u.email, m.role FROM memberships m JOIN users u ON u.id = m.user_id
                              WHERE m.organization_id = :org AND u.is_active = :yes ORDER BY u.id""",
                    {"org": organization_id, "yes": 1})


# ---------------------------------------------------------------- requirements
def list_requirements(organization_id: int, case_id: int, include_archived: bool = False) -> list:
    where = "" if include_archived else " AND archived_at IS NULL"
    with db_connection() as conn:
        return _all(conn, f"""SELECT * FROM program_requirements WHERE organization_id = :org AND change_case_id = :c{where}
                               ORDER BY is_ranking_target DESC, id""", {"org": organization_id, "c": case_id})


def get_requirement(organization_id: int, case_id: int, requirement_id: int):
    with db_connection() as conn:
        return _one(conn, "SELECT * FROM program_requirements WHERE id = :id AND organization_id = :org AND change_case_id = :c",
                    {"id": requirement_id, "org": organization_id, "c": case_id})


def create_requirement(organization_id: int, case_id: int, user_id, fields: dict, ranking_target: bool = False) -> int:
    now = now_iso()
    with db_transaction() as conn:
        return conn.execute(
            text("""INSERT INTO program_requirements (organization_id, change_case_id, property, threshold, direction, unit,
                        test_method, criticality, source, is_ranking_target, created_by_user_id, created_at, updated_at)
                    VALUES (:org, :c, :property, :threshold, :direction, :unit, :test_method, :criticality, :source, :rt, :u, :now, :now)
                    RETURNING id"""),
            {"org": organization_id, "c": case_id, "u": user_id, "now": now, "rt": 1 if ranking_target else 0,
             "property": fields["property"], "threshold": fields["threshold"], "direction": fields["direction"],
             "unit": fields.get("unit"), "test_method": fields.get("test_method"),
             "criticality": fields.get("criticality", "important"), "source": fields.get("source")}).scalar()


_REQ_COLUMNS = ("property", "threshold", "direction", "unit", "test_method", "criticality", "source")


def update_requirement(organization_id: int, requirement_id: int, fields: dict) -> None:
    unknown = set(fields) - set(_REQ_COLUMNS)
    if unknown or not fields:
        raise ValueError("Not editable")
    assignments = ", ".join(f"{c} = :{c}" for c in fields)
    with db_transaction() as conn:
        conn.execute(text(f"UPDATE program_requirements SET {assignments}, updated_at = :now WHERE id = :id AND organization_id = :org"),
                     {**fields, "now": now_iso(), "id": requirement_id, "org": organization_id})


def sync_ranking_target(organization_id: int, case_id: int, threshold: float, direction: str) -> None:
    """Keeps the register row of the ranking target equal to the spec the ranking reads."""
    with db_transaction() as conn:
        conn.execute(text("""UPDATE program_requirements SET threshold = :t, direction = :d, updated_at = :now
                              WHERE organization_id = :org AND change_case_id = :c AND is_ranking_target = 1"""),
                     {"t": threshold, "d": direction, "now": now_iso(), "org": organization_id, "c": case_id})


def set_requirement_archived(organization_id: int, requirement_id: int, archived: bool) -> None:
    with db_transaction() as conn:
        conn.execute(text("UPDATE program_requirements SET archived_at = :a, updated_at = :now WHERE id = :id AND organization_id = :org"),
                     {"a": now_iso() if archived else None, "now": now_iso(), "id": requirement_id, "org": organization_id})


def add_result(organization_id: int, requirement_id: int, user_id, result_text: str, met: bool, reference) -> int:
    with db_transaction() as conn:
        return conn.execute(
            text("""INSERT INTO requirement_results (organization_id, requirement_id, result_text, met, test_reference,
                                                     recorded_by_user_id, created_at)
                    VALUES (:org, :r, :t, :m, :ref, :u, :now) RETURNING id"""),
            {"org": organization_id, "r": requirement_id, "t": result_text, "m": 1 if met else 0, "ref": reference,
             "u": user_id, "now": now_iso()}).scalar()


def list_results(organization_id: int, case_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT r.id, r.requirement_id, r.result_text, r.met, r.test_reference, r.created_at,
                                    u.display_name AS actor_name, u.email AS actor_email
                               FROM requirement_results r JOIN program_requirements q ON q.id = r.requirement_id
                               LEFT JOIN users u ON u.id = r.recorded_by_user_id
                              WHERE r.organization_id = :org AND q.change_case_id = :c ORDER BY r.id DESC""",
                    {"org": organization_id, "c": case_id})


def support_facts(organization_id: int, case_id: int) -> dict:
    """Everything the completion and approval gates need, from stored records."""
    reqs = list_requirements(organization_id, case_id)
    with db_connection() as conn:
        passed = conn.execute(
            text("""SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
                     WHERE cs.change_case_id = :c AND cs.organization_id = :org AND o.passed_spec = :yes"""),
            {"c": case_id, "org": organization_id, "yes": 1}).scalar()
        outcomes = conn.execute(
            text("""SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
                     WHERE cs.change_case_id = :c AND cs.organization_id = :org"""), {"c": case_id, "org": organization_id}).scalar()
        latest = {}
        for r in _all(conn, """SELECT r.requirement_id, r.met FROM requirement_results r
                                 JOIN program_requirements q ON q.id = r.requirement_id
                                WHERE q.change_case_id = :c AND r.organization_id = :org ORDER BY r.id""",
                      {"c": case_id, "org": organization_id}):
            latest[r["requirement_id"]] = bool(r["met"])  # later rows overwrite earlier ones: the latest result counts
    return {"requirements": reqs, "passed_outcome_count": passed, "outcome_count": outcomes, "latest_met": latest}


# ---------------------------------------------------------------- reviewers
def list_reviewers(organization_id: int, case_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT p.user_id, p.created_at, u.display_name, u.email FROM program_reviewers p
                               JOIN users u ON u.id = p.user_id
                              WHERE p.organization_id = :org AND p.change_case_id = :c ORDER BY p.id""",
                    {"org": organization_id, "c": case_id})


def add_reviewer(organization_id: int, case_id: int, user_id: int, added_by) -> bool:
    with db_transaction() as conn:
        if conn.execute(text("SELECT 1 FROM program_reviewers WHERE change_case_id = :c AND user_id = :u"),
                        {"c": case_id, "u": user_id}).first():
            return False
        conn.execute(text("""INSERT INTO program_reviewers (organization_id, change_case_id, user_id, added_by_user_id, created_at)
                              VALUES (:org, :c, :u, :by, :now)"""),
                     {"org": organization_id, "c": case_id, "u": user_id, "by": added_by, "now": now_iso()})
        return True


def remove_reviewer(organization_id: int, case_id: int, user_id: int) -> bool:
    with db_transaction() as conn:
        return conn.execute(text("DELETE FROM program_reviewers WHERE organization_id = :org AND change_case_id = :c AND user_id = :u"),
                            {"org": organization_id, "c": case_id, "u": user_id}).rowcount > 0


def is_reviewer(organization_id: int, case_id: int, user_id: int) -> bool:
    with db_connection() as conn:
        return conn.execute(text("SELECT 1 FROM program_reviewers WHERE organization_id = :org AND change_case_id = :c AND user_id = :u"),
                            {"org": organization_id, "c": case_id, "u": user_id}).first() is not None


# ---------------------------------------------------------------- milestones and tasks
def list_milestones(organization_id: int, case_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT id, title, due_date, completed_at, created_at FROM program_milestones
                              WHERE organization_id = :org AND change_case_id = :c ORDER BY due_date, id""",
                    {"org": organization_id, "c": case_id})


def get_milestone(organization_id: int, case_id: int, milestone_id: int):
    with db_connection() as conn:
        return _one(conn, "SELECT * FROM program_milestones WHERE id = :id AND organization_id = :org AND change_case_id = :c",
                    {"id": milestone_id, "org": organization_id, "c": case_id})


def create_milestone(organization_id: int, case_id: int, user_id, title: str, due_date: str) -> int:
    with db_transaction() as conn:
        return conn.execute(
            text("""INSERT INTO program_milestones (organization_id, change_case_id, title, due_date, created_by_user_id, created_at)
                    VALUES (:org, :c, :t, :d, :u, :now) RETURNING id"""),
            {"org": organization_id, "c": case_id, "t": title, "d": due_date, "u": user_id, "now": now_iso()}).scalar()


def update_milestone(organization_id: int, milestone_id: int, fields: dict) -> None:
    allowed = {"title", "due_date", "completed_at", "completed_by_user_id"}
    if set(fields) - allowed or not fields:
        raise ValueError("Not editable")
    assignments = ", ".join(f"{c} = :{c}" for c in fields)
    with db_transaction() as conn:
        conn.execute(text(f"UPDATE program_milestones SET {assignments} WHERE id = :id AND organization_id = :org"),
                     {**fields, "id": milestone_id, "org": organization_id})


def delete_milestone(organization_id: int, milestone_id: int) -> None:
    with db_transaction() as conn:
        conn.execute(text("DELETE FROM program_milestones WHERE id = :id AND organization_id = :org"),
                     {"id": milestone_id, "org": organization_id})


def list_tasks(organization_id: int, case_id: int) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT t.id, t.title, t.assignee_user_id, t.due_date, t.completed_at, t.created_at,
                                    a.display_name AS assignee_name, a.email AS assignee_email
                               FROM program_tasks t LEFT JOIN users a ON a.id = t.assignee_user_id
                              WHERE t.organization_id = :org AND t.change_case_id = :c ORDER BY t.id""",
                    {"org": organization_id, "c": case_id})


def get_task(organization_id: int, case_id: int, task_id: int):
    with db_connection() as conn:
        return _one(conn, "SELECT * FROM program_tasks WHERE id = :id AND organization_id = :org AND change_case_id = :c",
                    {"id": task_id, "org": organization_id, "c": case_id})


def create_task(organization_id: int, case_id: int, user_id, title: str, assignee_id, due_date) -> int:
    with db_transaction() as conn:
        return conn.execute(
            text("""INSERT INTO program_tasks (organization_id, change_case_id, title, assignee_user_id, due_date, created_by_user_id, created_at)
                    VALUES (:org, :c, :t, :a, :d, :u, :now) RETURNING id"""),
            {"org": organization_id, "c": case_id, "t": title, "a": assignee_id, "d": due_date, "u": user_id,
             "now": now_iso()}).scalar()


def set_task_done(organization_id: int, task_id: int, user_id, done: bool) -> None:
    with db_transaction() as conn:
        conn.execute(text("UPDATE program_tasks SET completed_at = :at, completed_by_user_id = :u WHERE id = :id AND organization_id = :org"),
                     {"at": now_iso() if done else None, "u": user_id if done else None, "id": task_id, "org": organization_id})


def delete_task(organization_id: int, task_id: int) -> None:
    with db_transaction() as conn:
        conn.execute(text("DELETE FROM program_tasks WHERE id = :id AND organization_id = :org"), {"id": task_id, "org": organization_id})


# ---------------------------------------------------------------- comments and timeline
def list_comments(organization_id: int, case_id: int, limit: int = 200) -> list:
    with db_connection() as conn:
        return _all(conn, """SELECT c.id, c.kind, c.body, c.created_at, c.user_id, u.display_name AS actor_name, u.email AS actor_email
                               FROM program_comments c LEFT JOIN users u ON u.id = c.user_id
                              WHERE c.organization_id = :org AND c.change_case_id = :cid ORDER BY c.id DESC LIMIT :limit""",
                    {"org": organization_id, "cid": case_id, "limit": limit})


def add_comment(organization_id: int, case_id: int, user_id, kind: str, body: str) -> int:
    with db_transaction() as conn:
        return conn.execute(
            text("""INSERT INTO program_comments (organization_id, change_case_id, user_id, kind, body, created_at)
                    VALUES (:org, :c, :u, :k, :b, :now) RETURNING id"""),
            {"org": organization_id, "c": case_id, "u": user_id, "k": kind, "b": body, "now": now_iso()}).scalar()


def _names_program(detail, case_id: int) -> bool:
    """True only when the detail holds the token change_case_id=<id> as a whole: at the start of the text or after whitespace,
    and followed by whitespace or the end, so neither a longer id nor a longer key name can match."""
    return bool(detail) and re.search(rf"(?:^|\s)change_case_id={case_id}(?:\s|$)", detail) is not None


def timeline(organization_id: int, case_id: int, limit: int = 100) -> list:
    """Audit rows whose detail names this program, newest first. SQL only narrows the candidates (a LIKE pattern treats the
    underscores as wildcards and cannot test a token boundary), then every candidate is checked exactly."""
    out, before = [], None
    while len(out) < limit:
        params = {"org": organization_id, "like": f"%change_case_id={case_id}%", "batch": 500}
        extra = ""
        if before is not None:
            extra, params["before"] = " AND a.id < :before", before
        with db_connection() as conn:
            rows = _all(conn, f"""SELECT a.id, a.action, a.detail, a.created_at, u.display_name AS actor_name, u.email AS actor_email
                                    FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
                                   WHERE a.organization_id = :org AND a.detail LIKE :like{extra}
                                   ORDER BY a.id DESC LIMIT :batch""", params)
        if not rows:
            break
        out.extend(r for r in rows if _names_program(r["detail"], case_id))
        before = rows[-1]["id"]
        if len(rows) < 500:
            break
    return out[:limit]
