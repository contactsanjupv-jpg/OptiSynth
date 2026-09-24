import json

from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


# ---------------------------------------------------------------------------
# candidate_substitutes
# ---------------------------------------------------------------------------

def create_candidate(organization_id: int, change_case_id: int, candidate_name: str,
                      properties: dict) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO candidate_substitutes
                     (organization_id, change_case_id, candidate_name, properties_json, created_at)
                     VALUES (:org_id, :change_case_id, :name, :properties_json, :created_at)
                     RETURNING id"""),
            {"org_id": organization_id, "change_case_id": change_case_id, "name": candidate_name,
             "properties_json": json.dumps(properties), "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_candidates_for_change_case(organization_id: int, change_case_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM candidate_substitutes
                     WHERE organization_id = :org_id AND change_case_id = :change_case_id
                     ORDER BY id ASC"""),
            {"org_id": organization_id, "change_case_id": change_case_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def count_candidates_for_change_case(organization_id: int, change_case_id: int) -> int:
    with db_connection() as conn:
        result = conn.execute(
            text("""SELECT COUNT(*) FROM candidate_substitutes
                     WHERE organization_id = :org_id AND change_case_id = :change_case_id"""),
            {"org_id": organization_id, "change_case_id": change_case_id},
        ).scalar()
        return result or 0


def get_candidate(organization_id: int, candidate_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM candidate_substitutes WHERE id = :id AND organization_id = :org_id"),
            {"id": candidate_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# predictions
# ---------------------------------------------------------------------------

def create_prediction(organization_id: int, candidate_id: int, dataset_version_id: int,
                       model_version: str, predicted_probability: float, uncertainty_std: float) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO predictions
                     (organization_id, candidate_id, dataset_version_id, model_version,
                      predicted_probability, uncertainty_std, created_at)
                     VALUES (:org_id, :candidate_id, :dataset_version_id, :model_version,
                             :predicted_probability, :uncertainty_std, :created_at)
                     RETURNING id"""),
            {"org_id": organization_id, "candidate_id": candidate_id,
             "dataset_version_id": dataset_version_id, "model_version": model_version,
             "predicted_probability": predicted_probability, "uncertainty_std": uncertainty_std,
             "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_latest_prediction_for_candidate(organization_id: int, candidate_id: int):
    """Ranking can be re-run (e.g. after a dataset re-upload) -- this
    always returns the MOST RECENT prediction, never an average or a
    stale cached one, preserving the 'exactly which data/model produced
    this recommendation' provenance guarantee."""
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT * FROM predictions WHERE organization_id = :org_id AND candidate_id = :candidate_id
                     ORDER BY id DESC LIMIT 1"""),
            {"org_id": organization_id, "candidate_id": candidate_id},
        ).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# recommended_experiments
# ---------------------------------------------------------------------------

def create_recommended_experiment(organization_id: int, candidate_id: int, description: str,
                                   priority: int = 0) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO recommended_experiments
                     (organization_id, candidate_id, description, priority, created_at)
                     VALUES (:org_id, :candidate_id, :description, :priority, :created_at)
                     RETURNING id"""),
            {"org_id": organization_id, "candidate_id": candidate_id, "description": description,
             "priority": priority, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_recommended_experiments_for_candidate(organization_id: int, candidate_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM recommended_experiments
                     WHERE organization_id = :org_id AND candidate_id = :candidate_id
                     ORDER BY priority DESC, id ASC"""),
            {"org_id": organization_id, "candidate_id": candidate_id},
        ).mappings().all()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# qualification_outcomes -- APPEND-ONLY. No update_* or delete_* function
# exists here, deliberately -- see PROJECT_ARCHITECTURE.md rule 4 and
# change_case_rules.check_outcome_not_already_recorded, which the service
# layer MUST call before ever reaching create_outcome below.
# ---------------------------------------------------------------------------

def count_outcomes_for_candidate(organization_id: int, candidate_id: int) -> int:
    """Feeds change_case_rules.check_outcome_not_already_recorded -- call
    this BEFORE create_outcome, always."""
    with db_connection() as conn:
        result = conn.execute(
            text("""SELECT COUNT(*) FROM qualification_outcomes
                     WHERE organization_id = :org_id AND candidate_id = :candidate_id"""),
            {"org_id": organization_id, "candidate_id": candidate_id},
        ).scalar()
        return result or 0


def create_outcome(organization_id: int, candidate_id: int, recommended_experiment_id,
                    actual_result: str, passed_spec: bool, recorded_by_user_id: int) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO qualification_outcomes
                     (organization_id, candidate_id, recommended_experiment_id, actual_result,
                      passed_spec, recorded_by_user_id, created_at)
                     VALUES (:org_id, :candidate_id, :rec_exp_id, :actual_result,
                             :passed_spec, :recorded_by, :created_at)
                     RETURNING id"""),
            {"org_id": organization_id, "candidate_id": candidate_id,
             "rec_exp_id": recommended_experiment_id, "actual_result": actual_result,
             "passed_spec": 1 if passed_spec else 0, "recorded_by": recorded_by_user_id,
             "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_outcomes_for_change_case(organization_id: int, change_case_id: int):
    """Joins through candidate_substitutes since qualification_outcomes
    doesn't carry change_case_id directly (it belongs to a candidate,
    which belongs to a change case) -- used by the report builder."""
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT qo.* FROM qualification_outcomes qo
                     JOIN candidate_substitutes cs ON cs.id = qo.candidate_id
                     WHERE qo.organization_id = :org_id AND cs.change_case_id = :change_case_id
                     ORDER BY qo.id ASC"""),
            {"org_id": organization_id, "change_case_id": change_case_id},
        ).mappings().all()
        return [dict(r) for r in rows]
