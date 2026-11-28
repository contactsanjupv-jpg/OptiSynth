"""Decision engine storage. Every query is scoped to the organization and the program. Analyses are append only: this
module has no function that updates or deletes one."""
import json

from sqlalchemy import text

from backend.app.config.database import db_connection, db_transaction
from backend.app.repositories._time import now_iso


def _row(r):
    return dict(r) if r is not None else None


# ------------------------------------------------------------------ attributes
def list_attributes(org, case_id, include_archived=True):
    sql = "SELECT * FROM decision_attributes WHERE organization_id = :o AND change_case_id = :c"
    if not include_archived:
        sql += " AND status = 'active'"
    with db_connection() as conn:
        return [dict(r) for r in conn.execute(text(sql + " ORDER BY id"), {"o": org, "c": case_id}).mappings()]


def get_attribute(org, case_id, attribute_id):
    with db_connection() as conn:
        return _row(conn.execute(text("SELECT * FROM decision_attributes WHERE id = :i AND organization_id = :o AND change_case_id = :c"),
                                 {"i": attribute_id, "o": org, "c": case_id}).mappings().first())


def create_attribute(org, case_id, key, label, value_type, unit, category, description, user_id):
    now = now_iso()
    with db_transaction() as conn:
        new_id = conn.execute(text("""INSERT INTO decision_attributes (organization_id, change_case_id, attr_key, label, value_type, unit, category,
                 description, status, created_by_user_id, created_at, updated_at)
                 VALUES (:o, :c, :k, :l, :t, :u, :cat, :d, 'active', :user, :now, :now) RETURNING id"""),
                              {"o": org, "c": case_id, "k": key, "l": label, "t": value_type, "u": unit, "cat": category,
                               "d": description, "user": user_id, "now": now}).scalar()
    return get_attribute(org, case_id, new_id)


def update_attribute(org, case_id, attribute_id, fields: dict):
    allowed = {"label", "description", "category", "status"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if sets:
        assignments = ", ".join(f"{k} = :{k}" for k in sets)
        with db_transaction() as conn:
            conn.execute(text(f"UPDATE decision_attributes SET {assignments}, updated_at = :now WHERE id = :i AND organization_id = :o AND change_case_id = :c"),
                         {**sets, "now": now_iso(), "i": attribute_id, "o": org, "c": case_id})
    return get_attribute(org, case_id, attribute_id)


# ------------------------------------------------------------------ values
def list_values(org, case_id):
    with db_connection() as conn:
        rows = conn.execute(text("SELECT * FROM candidate_attribute_values WHERE organization_id = :o AND change_case_id = :c ORDER BY id"),
                            {"o": org, "c": case_id}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["value"] = json.loads(d.pop("value_json"))
        out.append(d)
    return out


def get_value(org, case_id, candidate_id, attribute_id):
    with db_connection() as conn:
        r = conn.execute(text("""SELECT * FROM candidate_attribute_values WHERE organization_id = :o AND change_case_id = :c
                                  AND candidate_id = :cand AND attribute_id = :a"""),
                         {"o": org, "c": case_id, "cand": candidate_id, "a": attribute_id}).mappings().first()
    if r is None:
        return None
    d = dict(r)
    d["value"] = json.loads(d.pop("value_json"))
    return d


def set_value(org, case_id, candidate_id, attribute_id, value, source_note, user_id):
    now = now_iso()
    with db_transaction() as conn:
        updated = conn.execute(text("""UPDATE candidate_attribute_values SET value_json = :v, source_note = :s, entered_by_user_id = :u, entered_at = :now
                                        WHERE organization_id = :o AND change_case_id = :c AND candidate_id = :cand AND attribute_id = :a"""),
                               {"v": json.dumps(value), "s": source_note, "u": user_id, "now": now, "o": org, "c": case_id, "cand": candidate_id, "a": attribute_id}).rowcount
        if not updated:
            conn.execute(text("""INSERT INTO candidate_attribute_values (organization_id, change_case_id, candidate_id, attribute_id, value_json,
                     source_note, entered_by_user_id, entered_at) VALUES (:o, :c, :cand, :a, :v, :s, :u, :now)"""),
                         {"o": org, "c": case_id, "cand": candidate_id, "a": attribute_id, "v": json.dumps(value), "s": source_note, "u": user_id, "now": now})
    return get_value(org, case_id, candidate_id, attribute_id)


def clear_value(org, case_id, candidate_id, attribute_id) -> bool:
    with db_transaction() as conn:
        return bool(conn.execute(text("""DELETE FROM candidate_attribute_values WHERE organization_id = :o AND change_case_id = :c
                                          AND candidate_id = :cand AND attribute_id = :a"""),
                                 {"o": org, "c": case_id, "cand": candidate_id, "a": attribute_id}).rowcount)


def count_values_for_attribute(org, case_id, attribute_id) -> int:
    with db_connection() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM candidate_attribute_values WHERE organization_id = :o AND change_case_id = :c AND attribute_id = :a"),
                                {"o": org, "c": case_id, "a": attribute_id}).scalar())


# ------------------------------------------------------------------ constraints and objectives
def _decode(r):
    d = dict(r)
    if "value_json" in d:
        d["value"] = json.loads(d.pop("value_json"))
    return d


def list_constraints(org, case_id, include_archived=True):
    sql = "SELECT * FROM decision_constraints WHERE organization_id = :o AND change_case_id = :c"
    if not include_archived:
        sql += " AND status = 'active'"
    with db_connection() as conn:
        return [_decode(r) for r in conn.execute(text(sql + " ORDER BY id"), {"o": org, "c": case_id}).mappings()]


def get_constraint(org, case_id, constraint_id):
    with db_connection() as conn:
        r = conn.execute(text("SELECT * FROM decision_constraints WHERE id = :i AND organization_id = :o AND change_case_id = :c"),
                         {"i": constraint_id, "o": org, "c": case_id}).mappings().first()
    return _decode(r) if r else None


def create_constraint(org, case_id, f: dict, user_id):
    now = now_iso()
    with db_transaction() as conn:
        new_id = conn.execute(text("""INSERT INTO decision_constraints (organization_id, change_case_id, name, kind, category, ref, operator, value_json,
                 unit, rationale, status, created_by_user_id, created_at, updated_at)
                 VALUES (:o, :c, :name, :kind, :category, :ref, :operator, :v, :unit, :rationale, 'active', :u, :now, :now) RETURNING id"""),
                              {"o": org, "c": case_id, "name": f["name"], "kind": f["kind"], "category": f["category"], "ref": f["ref"],
                               "operator": f["operator"], "v": json.dumps(f["value"]), "unit": f.get("unit"), "rationale": f["rationale"],
                               "u": user_id, "now": now}).scalar()
    return get_constraint(org, case_id, new_id)


def update_constraint(org, case_id, constraint_id, f: dict):
    sets, params = [], {"now": now_iso(), "i": constraint_id, "o": org, "c": case_id}
    for k in ("name", "kind", "category", "ref", "operator", "unit", "rationale", "status"):
        if k in f:
            sets.append(f"{k} = :{k}")
            params[k] = f[k]
    if "value" in f:
        sets.append("value_json = :v")
        params["v"] = json.dumps(f["value"])
    if sets:
        with db_transaction() as conn:
            conn.execute(text(f"UPDATE decision_constraints SET {', '.join(sets)}, updated_at = :now WHERE id = :i AND organization_id = :o AND change_case_id = :c"), params)
    return get_constraint(org, case_id, constraint_id)


def list_objectives(org, case_id, include_archived=True):
    sql = "SELECT * FROM decision_objectives WHERE organization_id = :o AND change_case_id = :c"
    if not include_archived:
        sql += " AND status = 'active'"
    with db_connection() as conn:
        return [dict(r) for r in conn.execute(text(sql + " ORDER BY id"), {"o": org, "c": case_id}).mappings()]


def get_objective(org, case_id, objective_id):
    with db_connection() as conn:
        return _row(conn.execute(text("SELECT * FROM decision_objectives WHERE id = :i AND organization_id = :o AND change_case_id = :c"),
                                 {"i": objective_id, "o": org, "c": case_id}).mappings().first())


def create_objective(org, case_id, name, ref, direction, unit, user_id):
    now = now_iso()
    with db_transaction() as conn:
        new_id = conn.execute(text("""INSERT INTO decision_objectives (organization_id, change_case_id, name, ref, direction, unit, status, created_by_user_id,
                 created_at, updated_at) VALUES (:o, :c, :n, :r, :d, :unit, 'active', :u, :now, :now) RETURNING id"""),
                              {"o": org, "c": case_id, "n": name, "r": ref, "d": direction, "unit": unit, "u": user_id, "now": now}).scalar()
    return get_objective(org, case_id, new_id)


def update_objective(org, case_id, objective_id, f: dict):
    sets, params = [], {"now": now_iso(), "i": objective_id, "o": org, "c": case_id}
    for k in ("name", "direction", "status"):
        if k in f:
            sets.append(f"{k} = :{k}")
            params[k] = f[k]
    if sets:
        with db_transaction() as conn:
            conn.execute(text(f"UPDATE decision_objectives SET {', '.join(sets)}, updated_at = :now WHERE id = :i AND organization_id = :o AND change_case_id = :c"), params)
    return get_objective(org, case_id, objective_id)


# ------------------------------------------------------------------ analyses (append only)
def create_analysis(org, case_id, input_hash, engine_version, snapshot, result, summary, user_id):
    with db_transaction() as conn:
        new_id = conn.execute(text("""INSERT INTO decision_analyses (organization_id, change_case_id, input_hash, engine_version, snapshot_json,
                 result_json, summary_json, created_by_user_id, created_at)
                 VALUES (:o, :c, :h, :e, :s, :r, :sum, :u, :now) RETURNING id"""),
                              {"o": org, "c": case_id, "h": input_hash, "e": engine_version, "s": json.dumps(snapshot), "r": json.dumps(result),
                               "sum": json.dumps(summary), "u": user_id, "now": now_iso()}).scalar()
    return get_analysis(org, case_id, new_id)


def get_analysis(org, case_id, analysis_id):
    with db_connection() as conn:
        r = conn.execute(text("SELECT * FROM decision_analyses WHERE id = :i AND organization_id = :o AND change_case_id = :c"),
                         {"i": analysis_id, "o": org, "c": case_id}).mappings().first()
    if r is None:
        return None
    d = dict(r)
    for k in ("snapshot", "result", "summary"):
        d[k] = json.loads(d.pop(f"{k}_json"))
    return d


def list_analyses(org, case_id, limit=20):
    with db_connection() as conn:
        rows = conn.execute(text("""SELECT id, input_hash, engine_version, summary_json, created_by_user_id, created_at FROM decision_analyses
                 WHERE organization_id = :o AND change_case_id = :c ORDER BY id DESC LIMIT :l"""), {"o": org, "c": case_id, "l": limit}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        d["summary"] = json.loads(d.pop("summary_json"))
        out.append(d)
    return out


def latest_analysis_id(org, case_id):
    with db_connection() as conn:
        return conn.execute(text("SELECT MAX(id) FROM decision_analyses WHERE organization_id = :o AND change_case_id = :c"), {"o": org, "c": case_id}).scalar()


# ------------------------------------------------------------------ physical outcomes (read only)
def outcome_summary(org, case_id) -> dict:
    with db_connection() as conn:
        rows = conn.execute(text("""SELECT qo.candidate_id, qo.passed_spec, COUNT(*) AS n FROM qualification_outcomes qo
                 JOIN candidate_substitutes cs ON cs.id = qo.candidate_id
                 WHERE qo.organization_id = :o AND cs.change_case_id = :c GROUP BY qo.candidate_id, qo.passed_spec"""),
                            {"o": org, "c": case_id}).mappings().all()
    out = {}
    for r in rows:
        d = out.setdefault(r["candidate_id"], {"passed": 0, "failed": 0})
        d["passed" if r["passed_spec"] else "failed"] += int(r["n"])
    return out
