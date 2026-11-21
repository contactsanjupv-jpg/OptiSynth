"""Stage of a program and its history. Every query is scoped by organization_id."""
from sqlalchemy import text

from backend.app.config.database import db_connection, db_transaction
from backend.app.repositories._time import now_iso


def program_facts(organization_id: int, change_case_id: int):
    """Counts of the real records a stage change is checked against (None if not this organization's program)."""
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT c.id, c.status, c.stage, c.archived_at,
                    (SELECT COUNT(*) FROM qualification_datasets d WHERE d.change_case_id = c.id) AS dataset_count,
                    (SELECT COUNT(*) FROM candidate_substitutes cs WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL) AS candidate_count,
                    (SELECT COUNT(*) FROM predictions p JOIN candidate_substitutes cs ON cs.id = p.candidate_id
                      WHERE cs.change_case_id = c.id AND cs.archived_at IS NULL) AS prediction_count,
                    (SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
                      WHERE cs.change_case_id = c.id) AS outcome_count,
                    (SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
                      WHERE cs.change_case_id = c.id AND o.passed_spec = :yes) AS passed_outcome_count
                    FROM change_cases c WHERE c.id = :id AND c.organization_id = :org"""),
            {"id": change_case_id, "org": organization_id, "yes": 1},
        ).mappings().first()
        return dict(row) if row else None


def set_stage(organization_id: int, change_case_id: int, from_stage: str, to_stage: str, reason, actor_user_id) -> bool:
    """Moves the stage and writes the history row in ONE transaction. The update only applies if the program is still at
    `from_stage`, so two people changing it at once cannot both succeed from the same starting point."""
    now = now_iso()
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE change_cases SET stage = :to, stage_changed_at = :now, updated_at = :now
                     WHERE id = :id AND organization_id = :org AND stage = :from_stage"""),
            {"to": to_stage, "now": now, "id": change_case_id, "org": organization_id, "from_stage": from_stage},
        )
        if result.rowcount == 0:
            return False
        conn.execute(
            text("""INSERT INTO program_stage_history
                     (organization_id, change_case_id, from_stage, to_stage, reason, actor_user_id, created_at)
                     VALUES (:org, :id, :from_stage, :to, :reason, :actor, :now)"""),
            {"org": organization_id, "id": change_case_id, "from_stage": from_stage, "to": to_stage,
             "reason": reason or None, "actor": actor_user_id, "now": now},
        )
        return True


def history(organization_id: int, change_case_id: int, limit: int = 100):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT h.id, h.from_stage, h.to_stage, h.reason, h.created_at, h.actor_user_id,
                           u.display_name AS actor_name, u.email AS actor_email
                      FROM program_stage_history h LEFT JOIN users u ON u.id = h.actor_user_id
                     WHERE h.organization_id = :org AND h.change_case_id = :id
                     ORDER BY h.id DESC LIMIT :limit"""),
            {"org": organization_id, "id": change_case_id, "limit": limit},
        ).mappings().all()
        return [dict(r) for r in rows]
