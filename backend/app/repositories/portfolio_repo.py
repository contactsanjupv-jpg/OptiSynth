"""Read only queries behind the overview. Every query is scoped by organization_id; nothing here writes."""
from datetime import datetime, timezone

from sqlalchemy import text

from backend.app.config.database import db_connection

# Per program counts, computed in one query so the overview does not make a query per program.
_FACTS_SQL = """
SELECT c.id, c.name, c.status, c.stage, c.trigger_type, c.created_at, c.updated_at, c.stage_changed_at,
       u.display_name AS creator_name, u.email AS creator_email,
  (SELECT COUNT(*) FROM qualification_datasets d WHERE d.change_case_id = c.id AND d.review_status = 'accepted') AS dataset_count,
  (SELECT COUNT(*) FROM qualification_datasets d WHERE d.change_case_id = c.id AND d.review_status IN ('clarification_requested', 'conflict_flagged')) AS held_evidence_count,
  (SELECT COUNT(*) FROM evidence_conflicts x WHERE x.change_case_id = c.id AND x.status = 'open' AND x.severity IN ('high', 'medium')) AS open_conflict_count,
  (SELECT COUNT(*) FROM candidate_substitutes cs WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL) AS candidate_count,
  (SELECT COUNT(*) FROM candidate_substitutes cs WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.candidate_id = cs.id)) AS unranked_count,
  (SELECT COUNT(*) FROM candidate_substitutes cs WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL
      AND EXISTS (SELECT 1 FROM predictions p WHERE p.candidate_id = cs.id)
      AND (SELECT p2.dataset_version_id FROM predictions p2 WHERE p2.candidate_id = cs.id ORDER BY p2.id DESC LIMIT 1)
          <> (SELECT MAX(d.id) FROM qualification_datasets d WHERE d.change_case_id = c.id AND d.review_status = 'accepted')) AS stale_ranked_count,
  (SELECT COUNT(*) FROM candidate_substitutes cs WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL
      AND (SELECT p3.inputs_version FROM predictions p3 WHERE p3.candidate_id = cs.id ORDER BY p3.id DESC LIMIT 1) IS NOT NULL
      AND (SELECT p3.inputs_version FROM predictions p3 WHERE p3.candidate_id = cs.id ORDER BY p3.id DESC LIMIT 1)
          <> cs.inputs_version) AS inputs_changed_count,
  (SELECT COUNT(DISTINCT e.candidate_id) FROM recommended_experiments e
      JOIN candidate_substitutes cs ON cs.id = e.candidate_id
     WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM qualification_outcomes o WHERE o.candidate_id = e.candidate_id)) AS pending_test_count,
  (SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
     WHERE cs.change_case_id = c.id AND o.passed_spec = :no) AS failed_outcome_count,
  (SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
     WHERE cs.change_case_id = c.id) AS outcome_count,
  (SELECT COUNT(*) FROM program_milestones m WHERE m.change_case_id = c.id AND m.completed_at IS NULL
      AND m.due_date < :today) AS overdue_milestone_count,
  (SELECT COUNT(*) FROM program_tasks t WHERE t.change_case_id = c.id AND t.completed_at IS NULL
      AND t.due_date IS NOT NULL AND t.due_date < :today) AS overdue_task_count
FROM change_cases c LEFT JOIN users u ON u.id = c.created_by_user_id
WHERE c.organization_id = :org AND c.archived_at IS NULL
ORDER BY c.updated_at DESC, c.id DESC
"""


def program_facts(organization_id: int) -> list:
    with db_connection() as conn:
        rows = conn.execute(text(_FACTS_SQL), {"org": organization_id, "no": 0, "today": datetime.now(timezone.utc).date().isoformat()}).mappings().all()
        return [dict(r) for r in rows]


def recent_audit(organization_id: int, since_iso, limit: int = 60) -> list:
    """Newest audit rows about programs. `since_iso` None means no lower bound."""
    where = "a.organization_id = :org AND (a.action LIKE 'change_case.%' OR a.action LIKE 'candidate.%' " \
            "OR a.action LIKE 'qualification_%')"
    params = {"org": organization_id, "limit": limit}
    if since_iso:
        where += " AND a.created_at >= :since"
        params["since"] = since_iso
    with db_connection() as conn:
        rows = conn.execute(
            text(f"""SELECT a.id, a.action, a.detail, a.created_at, u.display_name AS actor_name, u.email AS actor_email
                       FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
                      WHERE {where} ORDER BY a.id DESC LIMIT :limit"""), params).mappings().all()
        return [dict(r) for r in rows]


def recent_decisions(organization_id: int, since_iso, limit: int = 8) -> list:
    where = "h.organization_id = :org AND h.to_stage IN ('approved', 'rejected', 'closed')"
    params = {"org": organization_id, "limit": limit}
    if since_iso:
        where += " AND h.created_at >= :since"
        params["since"] = since_iso
    with db_connection() as conn:
        rows = conn.execute(
            text(f"""SELECT h.id, h.change_case_id, h.to_stage, h.reason, h.created_at, c.name AS program_name,
                            u.display_name AS actor_name, u.email AS actor_email
                       FROM program_stage_history h
                       JOIN change_cases c ON c.id = h.change_case_id
                       LEFT JOIN users u ON u.id = h.actor_user_id
                      WHERE {where} ORDER BY h.id DESC LIMIT :limit"""), params).mappings().all()
        return [dict(r) for r in rows]


def program_names(organization_id: int, ids: list) -> dict:
    """id -> name for programs of this organization, archived ones included."""
    if not ids:
        return {}
    marks = ", ".join(f":i{n}" for n in range(len(ids)))
    params = {f"i{n}": v for n, v in enumerate(ids)}
    params["org"] = organization_id
    with db_connection() as conn:
        rows = conn.execute(text(f"SELECT id, name FROM change_cases WHERE organization_id = :org AND id IN ({marks})"),
                            params).mappings().all()
        return {r["id"]: r["name"] for r in rows}
