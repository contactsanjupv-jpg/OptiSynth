from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_change_case(organization_id: int, created_by_user_id: int, name: str,
                        trigger_type: str, restricted_substance: str,
                        qualification_spec_json: str, details: dict = None, owner_user_id: int = None,
                        target: dict = None) -> int:
    """Creates the program and, in the same transaction, the register row for the target in its spec (the ranking
    target), so a program never exists without it."""
    import json
    details = details or {}
    now = now_iso()
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO change_cases
                     (organization_id, created_by_user_id, name, trigger_type,
                      restricted_substance, qualification_spec_json, status,
                      material_replaced, reason_for_substitution, business_impact, original_formulation,
                      manufacturing_constraints, target_date, owner_user_id,
                      created_at, updated_at)
                     VALUES (:org_id, :created_by, :name, :trigger_type,
                             :restricted_substance, :spec_json, 'draft',
                             :material_replaced, :reason_for_substitution, :business_impact, :original_formulation,
                             :manufacturing_constraints, :target_date, :owner,
                             :created_at, :updated_at) RETURNING id"""),
            {"org_id": organization_id, "created_by": created_by_user_id, "name": name,
             "trigger_type": trigger_type, "restricted_substance": restricted_substance,
             "spec_json": qualification_spec_json, "created_at": now, "updated_at": now,
             "material_replaced": details.get("material_replaced"), "reason_for_substitution": details.get("reason_for_substitution"),
             "business_impact": details.get("business_impact"), "original_formulation": details.get("original_formulation"),
             "manufacturing_constraints": details.get("manufacturing_constraints"), "target_date": details.get("target_date"),
             "owner": owner_user_id or created_by_user_id},
        ).mappings().first()
        case_id = row["id"]
        try:
            spec = json.loads(qualification_spec_json or "{}")
            metric, value, direction = spec["target_metric"], float(spec["target_value"]), spec["direction"]
        except (ValueError, KeyError, TypeError):
            return case_id
        target = target or {}
        conn.execute(
            text("""INSERT INTO program_requirements
                     (organization_id, change_case_id, property, threshold, direction, unit, test_method, criticality, source,
                      is_ranking_target, created_by_user_id, created_at, updated_at)
                     VALUES (:org, :c, :p, :t, :d, :u, :tm, :crit, :src, 1, :by, :now, :now)"""),
            {"org": organization_id, "c": case_id, "p": str(metric), "t": value, "d": direction,
             "u": (spec.get("units") or {}).get(metric), "tm": target.get("test_method"),
             "crit": target.get("criticality") or "important", "src": target.get("source") or "Program definition",
             "by": created_by_user_id, "now": now})
        return case_id


def get_change_case(organization_id: int, change_case_id: int):
    """Org-scoped lookup -- returns None if the change case doesn't exist
    OR belongs to a different organization, so callers get the same 404
    behavior for both cases (never distinguishing 'not found' from
    'not yours' to the client, matching project_routes.py's existing
    pattern)."""
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM change_cases WHERE id = :id AND organization_id = :org_id"),
            {"id": change_case_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None


def list_change_cases(organization_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM change_cases WHERE organization_id = :org_id ORDER BY id DESC"),
            {"org_id": organization_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def update_status(organization_id: int, change_case_id: int, new_status: str) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE change_cases SET status = :status, updated_at = :updated_at
                     WHERE id = :id AND organization_id = :org_id"""),
            {"status": new_status, "updated_at": now_iso(), "id": change_case_id, "org_id": organization_id},
        )
        return result.rowcount > 0


# ---------------------------------------------------------------------------
# Lifecycle management: usage counts, edit, archive, permanent delete
# ---------------------------------------------------------------------------

_USAGE_COLUMNS = """
    (SELECT COUNT(*) FROM qualification_datasets d WHERE d.change_case_id = c.id) AS dataset_count,
    (SELECT COUNT(*) FROM candidate_substitutes cs WHERE cs.change_case_id = c.id) AS candidate_count,
    (SELECT COUNT(*) FROM predictions p JOIN candidate_substitutes cs ON cs.id = p.candidate_id
      WHERE cs.change_case_id = c.id) AS prediction_count,
    (SELECT COUNT(*) FROM qualification_outcomes o JOIN candidate_substitutes cs ON cs.id = o.candidate_id
      WHERE cs.change_case_id = c.id) AS outcome_count
"""


def list_change_cases_with_usage(organization_id: int, include_archived: bool = False):
    """Every program of the organization (newest first) with how much depends on it. Archived
    programs are left out unless asked for."""
    where = "c.organization_id = :org_id" + ("" if include_archived else " AND c.archived_at IS NULL")
    with db_connection() as conn:
        rows = conn.execute(
            text(f"SELECT c.*, {_USAGE_COLUMNS} FROM change_cases c WHERE {where} ORDER BY c.id DESC"),
            {"org_id": organization_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def get_change_case_with_usage(organization_id: int, change_case_id: int):
    """Org-scoped lookup (None if missing or another organization's) with the usage counts."""
    with db_connection() as conn:
        row = conn.execute(
            text(f"""SELECT c.*, {_USAGE_COLUMNS} FROM change_cases c
                      WHERE c.id = :id AND c.organization_id = :org_id"""),
            {"id": change_case_id, "org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None


_EDITABLE_COLUMNS = ("name", "trigger_type", "restricted_substance", "qualification_spec_json")


def update_change_case_fields(organization_id: int, change_case_id: int, fields: dict) -> bool:
    """Updates only whitelisted columns of an org-owned program. The column names are never taken
    from the caller: anything outside the whitelist is refused."""
    unknown = set(fields) - set(_EDITABLE_COLUMNS)
    if unknown or not fields:
        raise ValueError(f"Not editable: {sorted(unknown)}" if unknown else "No fields to update.")
    assignments = ", ".join(f"{column} = :{column}" for column in fields)
    with db_transaction() as conn:
        result = conn.execute(
            text(f"""UPDATE change_cases SET {assignments}, updated_at = :updated_at
                      WHERE id = :id AND organization_id = :org_id"""),
            {**fields, "updated_at": now_iso(), "id": change_case_id, "org_id": organization_id},
        )
        return result.rowcount > 0


def set_archived(organization_id: int, change_case_id: int, archived: bool) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE change_cases SET archived_at = :archived_at, updated_at = :updated_at
                     WHERE id = :id AND organization_id = :org_id"""),
            {"archived_at": now_iso() if archived else None, "updated_at": now_iso(),
             "id": change_case_id, "org_id": organization_id},
        )
        return result.rowcount > 0


def delete_change_case_tree(organization_id: int, change_case_id: int):
    """Permanently deletes a program and everything under it, in ONE transaction, in explicit
    dependency order (so it behaves the same whether or not the database enforces foreign keys, and
    never leaves orphans). Returns the stored file names of its datasets so the caller can remove the
    files once the database change has committed -- or None if the program does not exist.
    The caller must already have decided that deletion is permitted
    (change_case_lifecycle_rules.check_can_delete): this function does not re-check."""
    params = {"org_id": organization_id, "case_id": change_case_id}
    candidate_ids = "SELECT id FROM candidate_substitutes WHERE organization_id = :org_id AND change_case_id = :case_id"
    with db_transaction() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM change_cases WHERE id = :case_id AND organization_id = :org_id"), params,
        ).first()
        if not exists:
            return None
        stored_files = [
            r[0] for r in conn.execute(
                text("""SELECT stored_filename FROM qualification_datasets
                         WHERE organization_id = :org_id AND change_case_id = :case_id"""), params,
            ).all()
        ]
        for table in ("evidence_reviews", "evidence_conflicts"):
            conn.execute(text(f"DELETE FROM {table} WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        for table in ("requirement_results",):
            conn.execute(text(f"DELETE FROM {table} WHERE requirement_id IN "
                              "(SELECT id FROM program_requirements WHERE organization_id = :org_id AND change_case_id = :case_id)"), params)
        for table in ("program_requirements", "program_reviewers", "program_milestones", "program_comments", "program_tasks",
                      "program_stage_history"):
            conn.execute(text(f"DELETE FROM {table} WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text(f"DELETE FROM qualification_outcomes WHERE candidate_id IN ({candidate_ids})"), params)
        conn.execute(text(f"DELETE FROM predictions WHERE candidate_id IN ({candidate_ids})"), params)
        conn.execute(text(f"DELETE FROM recommended_experiments WHERE candidate_id IN ({candidate_ids})"), params)
        conn.execute(text("DELETE FROM candidate_substitutes WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text("DELETE FROM qualification_experiments WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text("DELETE FROM qualification_datasets WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text("DELETE FROM change_cases WHERE id = :case_id AND organization_id = :org_id"), params)
        return stored_files
