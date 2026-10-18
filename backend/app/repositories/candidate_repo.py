import json

from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


class RankingConflict(Exception):
    """A candidate's inputs changed between reading it and saving the ranking computed from it, or
    the candidate no longer exists. Nothing from the run was saved."""


class CandidateHasDependents(Exception):
    """Permanent deletion refused inside the transaction: the candidate has predictions or recorded
    outcomes. The service normally refuses first; this is the last line of defence."""


# ---------------------------------------------------------------------------
# candidate_substitutes
# ---------------------------------------------------------------------------

def create_candidate(organization_id: int, change_case_id: int, candidate_name: str,
                      properties: dict, input_json: str = None) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO candidate_substitutes
                     (organization_id, change_case_id, candidate_name, properties_json, input_json, created_at)
                     VALUES (:org_id, :change_case_id, :name, :properties_json, :input_json, :created_at)
                     RETURNING id"""),
            {"org_id": organization_id, "change_case_id": change_case_id, "name": candidate_name,
             "properties_json": json.dumps(properties), "input_json": input_json, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def list_candidates_for_change_case(organization_id: int, change_case_id: int, include_archived: bool = False):
    """The diagnostic's candidates in id order. Candidates removed from the analysis (archived) are
    left out unless asked for: ranking, sufficiency and the default list never see them."""
    archived_filter = "" if include_archived else " AND archived_at IS NULL"
    with db_connection() as conn:
        rows = conn.execute(
            text(f"""SELECT * FROM candidate_substitutes
                     WHERE organization_id = :org_id AND change_case_id = :change_case_id{archived_filter}
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
# candidate lifecycle: usage, edit, remove-from-analysis (archive), permanent delete
# ---------------------------------------------------------------------------

def get_candidate_usage_map(organization_id: int, change_case_id: int) -> dict:
    """{candidate_id: {"prediction_count", "outcome_count", "experiment_count"}} for EVERY candidate of
    the diagnostic (archived ones included), in two grouped queries."""
    usage = {}
    with db_connection() as conn:
        ids = [r[0] for r in conn.execute(
            text("""SELECT id FROM candidate_substitutes
                     WHERE organization_id = :org_id AND change_case_id = :case_id"""),
            {"org_id": organization_id, "case_id": change_case_id}).all()]
        for cid in ids:
            usage[cid] = {"prediction_count": 0, "outcome_count": 0, "experiment_count": 0}
        for table, key in (("predictions", "prediction_count"), ("qualification_outcomes", "outcome_count"),
                           ("recommended_experiments", "experiment_count")):
            for cid, n in conn.execute(
                text(f"""SELECT t.candidate_id, COUNT(*) FROM {table} t
                          JOIN candidate_substitutes cs ON cs.id = t.candidate_id
                          WHERE cs.organization_id = :org_id AND cs.change_case_id = :case_id
                          GROUP BY t.candidate_id"""),
                {"org_id": organization_id, "case_id": change_case_id}).all():
                usage[cid][key] = n
    return usage


_EDITABLE_CANDIDATE_COLUMNS = ("candidate_name", "properties_json", "input_json")


def update_candidate_fields(organization_id: int, candidate_id: int, fields: dict,
                             bump_inputs_version: bool = False) -> bool:
    """Updates only whitelisted columns of an org-owned candidate (column names are never taken from
    the caller). `bump_inputs_version` increments inputs_version in the SAME statement, so the new
    values and the new version can never be stored apart."""
    unknown = set(fields) - set(_EDITABLE_CANDIDATE_COLUMNS)
    if unknown or not fields:
        raise ValueError(f"Not editable: {sorted(unknown)}" if unknown else "No fields to update.")
    assignments = ", ".join(f"{column} = :{column}" for column in fields)
    if bump_inputs_version:
        assignments += ", inputs_version = inputs_version + 1"
    with db_transaction() as conn:
        result = conn.execute(
            text(f"UPDATE candidate_substitutes SET {assignments} WHERE id = :id AND organization_id = :org_id"),
            {**fields, "id": candidate_id, "org_id": organization_id},
        )
        return result.rowcount > 0


def set_candidate_archived(organization_id: int, candidate_id: int, archived: bool) -> bool:
    """Removes a candidate from the analysis (archived_at = now) or puts it back (archived_at = NULL,
    restored_at = now). Nothing about the candidate, its predictions or its outcomes is deleted."""
    stamp = now_iso()
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE candidate_substitutes
                       SET archived_at = :archived_at, restored_at = COALESCE(:restored_at, restored_at)
                     WHERE id = :id AND organization_id = :org_id"""),
            {"archived_at": stamp if archived else None, "restored_at": None if archived else stamp,
             "id": candidate_id, "org_id": organization_id},
        )
        return result.rowcount > 0


def delete_candidate(organization_id: int, candidate_id: int) -> bool:
    """Permanently deletes one candidate nobody depends on. Re-checks inside the transaction that it
    has no predictions and no recorded outcomes (CandidateHasDependents otherwise) -- recorded
    outcomes are never deleted by this function. Returns False if the candidate does not exist."""
    params = {"id": candidate_id, "org_id": organization_id}
    with db_transaction() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM candidate_substitutes WHERE id = :id AND organization_id = :org_id"), params,
        ).first()
        if not exists:
            return False
        for table in ("predictions", "qualification_outcomes"):
            n = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE candidate_id = :id"), params).scalar() or 0
            if n:
                raise CandidateHasDependents(f"{table} reference this candidate")
        conn.execute(text("DELETE FROM recommended_experiments WHERE candidate_id = :id"), params)
        conn.execute(text("DELETE FROM candidate_substitutes WHERE id = :id AND organization_id = :org_id"), params)
        return True


# ---------------------------------------------------------------------------
# predictions
# ---------------------------------------------------------------------------

def create_prediction(organization_id: int, candidate_id: int, dataset_version_id: int,
                       model_version: str, predicted_probability: float, uncertainty_std: float,
                       predicted_value: float = None, inputs_version: int = 0) -> int:
    """Stores ONE prediction. predicted_value is the engine's predicted target
    property in the case's canonical unit; None means "not recorded" (it is
    never defaulted or estimated). A ranking run must NOT call this once per
    candidate -- use persist_ranking, which saves the whole run atomically."""
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO predictions
                     (organization_id, candidate_id, dataset_version_id, model_version,
                      predicted_probability, uncertainty_std, predicted_value, inputs_version, created_at)
                     VALUES (:org_id, :candidate_id, :dataset_version_id, :model_version,
                             :predicted_probability, :uncertainty_std, :predicted_value, :inputs_version, :created_at)
                     RETURNING id"""),
            {"org_id": organization_id, "candidate_id": candidate_id,
             "dataset_version_id": dataset_version_id, "model_version": model_version,
             "predicted_probability": predicted_probability, "uncertainty_std": uncertainty_std,
             "predicted_value": predicted_value, "inputs_version": inputs_version, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def persist_ranking(organization_id: int, dataset_version_id: int, rows: list) -> None:
    """Saves an ENTIRE ranking run -- one prediction and one recommended
    experiment per candidate -- in a single database transaction. If any insert
    fails, nothing from the run is kept (no half-saved ranking, no candidate
    with a prediction but no recommended experiment).

    rows: [{"candidate_id", "model_version", "predicted_value",
            "predicted_probability", "uncertainty_std",
            "experiment_description", "priority", "inputs_version"}]
    `inputs_version` (default 0) is the candidate's inputs_version the prediction was computed for;
    if the candidate was edited since it was read, the whole run is refused (RankingConflict) rather
    than saving a prediction labelled with values the candidate no longer has.
    Every row of one run shares one created_at, so the run is identifiable."""
    created_at = now_iso()
    with db_transaction() as conn:
        for r in rows:
            current = conn.execute(
                text("""SELECT inputs_version FROM candidate_substitutes
                         WHERE id = :id AND organization_id = :org_id"""),
                {"id": r["candidate_id"], "org_id": organization_id},
            ).first()
            if current is None or int(current[0] or 0) != int(r.get("inputs_version", 0) or 0):
                raise RankingConflict("A candidate changed while the ranking was running; nothing was saved.")
        for r in rows:
            _insert_prediction(conn, organization_id, dataset_version_id, r, created_at)
            _insert_recommended_experiment(conn, organization_id, r, created_at)


def _insert_prediction(conn, organization_id: int, dataset_version_id: int, r: dict, created_at: str) -> None:
    conn.execute(
        text("""INSERT INTO predictions
                 (organization_id, candidate_id, dataset_version_id, model_version,
                  predicted_probability, uncertainty_std, predicted_value, inputs_version, created_at)
                 VALUES (:org_id, :candidate_id, :dataset_version_id, :model_version,
                         :predicted_probability, :uncertainty_std, :predicted_value, :inputs_version, :created_at)"""),
        {"org_id": organization_id, "candidate_id": r["candidate_id"],
         "dataset_version_id": dataset_version_id, "model_version": r["model_version"],
         "predicted_probability": r["predicted_probability"], "uncertainty_std": r["uncertainty_std"],
         "predicted_value": r["predicted_value"], "inputs_version": int(r.get("inputs_version", 0) or 0),
         "created_at": created_at},
    )


def _insert_recommended_experiment(conn, organization_id: int, r: dict, created_at: str) -> None:
    conn.execute(
        text("""INSERT INTO recommended_experiments
                 (organization_id, candidate_id, description, priority, created_at)
                 VALUES (:org_id, :candidate_id, :description, :priority, :created_at)"""),
        {"org_id": organization_id, "candidate_id": r["candidate_id"],
         "description": r["experiment_description"], "priority": r["priority"], "created_at": created_at},
    )


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


def get_recommended_experiment(organization_id: int, recommended_experiment_id: int):
    """Org-scoped lookup of one recommended experiment (None if it does not exist or belongs to
    another organization)."""
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM recommended_experiments WHERE id = :id AND organization_id = :org_id"),
            {"id": recommended_experiment_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None


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