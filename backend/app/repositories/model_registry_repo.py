"""Model versions and validation records. Both are append only: this module has no update or delete function, so a
recorded identity or result cannot be changed through the application. Every query is scoped to the organization."""
import json

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.app.config.database import db_connection, db_transaction
from backend.app.repositories._time import now_iso

_JSON_VERSION = ("feature_definitions_json", "target_json", "preprocessing_json", "configuration_json",
                 "environment_json", "training_summary_json")


def _version(row):
    if row is None:
        return None
    out = dict(row)
    for key in _JSON_VERSION:
        out[key[:-5]] = json.loads(out.pop(key))
    return out


def _validation(row):
    if row is None:
        return None
    out = dict(row)
    out["result"] = json.loads(out.pop("result_json"))
    return out


def get_by_identity(organization_id: int, change_case_id: int, identity_hash: str):
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT * FROM model_versions WHERE organization_id = :org AND change_case_id = :case
                     AND identity_hash = :h"""),
            {"org": organization_id, "case": change_case_id, "h": identity_hash}).mappings().first()
    return _version(row)


def create_version(organization_id: int, change_case_id: int, identity: dict, dataset_id, feature_definitions: list,
                   target: dict, preprocessing: dict, configuration: dict, environment: dict, training_summary: dict,
                   seed: int, engine_version: str, family: str, n_rows: int, n_input_sets: int, source: str,
                   user_id) -> tuple:
    """Returns (version, created). An identical identity in the same program returns the existing record
    (created False): the same data and settings are the same model version, not a new one."""
    existing = get_by_identity(organization_id, change_case_id, identity["identity_hash"])
    if existing:
        return existing, False
    for _ in range(3):  # a concurrent registration of another identity can take the next number; try again
        try:
            with db_transaction() as conn:
                number = conn.execute(
                    text("SELECT COALESCE(MAX(version_number), 0) + 1 FROM model_versions WHERE change_case_id = :case"),
                    {"case": change_case_id}).scalar()
                new_id = conn.execute(
                    text("""INSERT INTO model_versions
                             (organization_id, change_case_id, version_number, model_key, identity_hash, training_data_hash,
                              dataset_id, family, engine_version, seed, feature_definitions_json, target_json,
                              preprocessing_json, configuration_json, environment_json, training_summary_json,
                              n_training_rows, n_input_sets, source, created_by_user_id, created_at)
                             VALUES (:org, :case, :number, :key, :h, :dh, :dataset, :family, :engine, :seed, :features,
                                     :target, :pre, :cfg, :env, :summary, :rows, :sets, :source, :user, :at) RETURNING id"""),
                    {"org": organization_id, "case": change_case_id, "number": number, "key": identity["model_key"],
                     "h": identity["identity_hash"], "dh": identity["training_data_hash"], "dataset": dataset_id,
                     "family": family, "engine": engine_version, "seed": seed, "features": json.dumps(feature_definitions),
                     "target": json.dumps(target), "pre": json.dumps(preprocessing), "cfg": json.dumps(configuration),
                     "env": json.dumps(environment), "summary": json.dumps(training_summary), "rows": n_rows,
                     "sets": n_input_sets, "source": source, "user": user_id, "at": now_iso()}).scalar()
            return get_version(organization_id, new_id), True
        except IntegrityError:
            existing = get_by_identity(organization_id, change_case_id, identity["identity_hash"])
            if existing:
                return existing, False
    raise RuntimeError("The model version could not be registered. Try again.")


def get_version(organization_id: int, version_id: int):
    with db_connection() as conn:
        row = conn.execute(text("SELECT * FROM model_versions WHERE id = :id AND organization_id = :org"),
                           {"id": version_id, "org": organization_id}).mappings().first()
    return _version(row)


def list_versions(organization_id: int, change_case_id: int = None, limit: int = 100, offset: int = 0) -> list:
    sql = ("SELECT mv.*, cc.name AS program_name FROM model_versions mv JOIN change_cases cc ON cc.id = mv.change_case_id "
           "WHERE mv.organization_id = :org")
    params = {"org": organization_id, "limit": limit, "offset": offset}
    if change_case_id is not None:
        sql += " AND mv.change_case_id = :case"
        params["case"] = change_case_id
    sql += " ORDER BY mv.id DESC LIMIT :limit OFFSET :offset"
    with db_connection() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [_version(r) for r in rows]


def count_versions(organization_id: int, change_case_id: int = None) -> int:
    sql, params = "SELECT COUNT(*) FROM model_versions WHERE organization_id = :org", {"org": organization_id}
    if change_case_id is not None:
        sql += " AND change_case_id = :case"
        params["case"] = change_case_id
    with db_connection() as conn:
        return int(conn.execute(text(sql), params).scalar())


def record_validation(organization_id: int, version: dict, protocol_key: str, result: dict, seed: int, engine_version: str,
                      user_id) -> dict:
    with db_transaction() as conn:
        new_id = conn.execute(
            text("""INSERT INTO model_validations
                     (organization_id, change_case_id, model_version_id, protocol_key, protocol_version, protocol_hash,
                      status, message, n_rows, n_scored, seed, engine_version, evaluation_rows_hash, fold_assignment_hash,
                      result_json, run_by_user_id, created_at)
                     VALUES (:org, :case, :version, :key, :pv, :ph, :status, :message, :rows, :scored, :seed, :engine,
                             :erh, :fah, :result, :user, :at) RETURNING id"""),
            {"org": organization_id, "case": version["change_case_id"], "version": version["id"], "key": protocol_key,
             "pv": result["protocol"]["version"], "ph": result["protocol"]["hash"], "status": result["status"],
             "message": result.get("message"), "rows": (result.get("counts") or {}).get("n_rows"),
             "scored": (result.get("counts") or {}).get("n_scored"), "seed": seed, "engine": engine_version,
             "erh": result.get("evaluation_rows_hash"), "fah": result.get("fold_assignment_hash"),
             "result": json.dumps(result), "user": user_id, "at": now_iso()}).scalar()
    return get_validation(organization_id, new_id)


def get_validation(organization_id: int, validation_id: int):
    with db_connection() as conn:
        row = conn.execute(text("SELECT * FROM model_validations WHERE id = :id AND organization_id = :org"),
                           {"id": validation_id, "org": organization_id}).mappings().first()
    return _validation(row)


def list_validations(organization_id: int, version_id: int) -> list:
    """Every record, newest first, failed and incomplete ones included."""
    with db_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM model_validations WHERE model_version_id = :v AND organization_id = :org ORDER BY id DESC"),
            {"v": version_id, "org": organization_id}).mappings().all()
    return [_validation(r) for r in rows]


def predictions_for_version(organization_id: int, version_id: int) -> list:
    """The latest prediction of each candidate that was made by this model version."""
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT p.*, cs.candidate_name, cs.properties_json, cs.inputs_version AS candidate_inputs_version,
                           cs.archived_at
                      FROM predictions p JOIN candidate_substitutes cs ON cs.id = p.candidate_id
                     WHERE p.organization_id = :org AND p.model_version_id = :v
                       AND p.id = (SELECT MAX(p2.id) FROM predictions p2 WHERE p2.candidate_id = p.candidate_id
                                      AND p2.model_version_id = :v)
                     ORDER BY p.id"""),
            {"org": organization_id, "v": version_id}).mappings().all()
    return [dict(r) for r in rows]


def prediction_count_for_version(organization_id: int, version_id: int) -> int:
    with db_connection() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM predictions WHERE organization_id = :org AND model_version_id = :v"),
                                {"org": organization_id, "v": version_id}).scalar())
