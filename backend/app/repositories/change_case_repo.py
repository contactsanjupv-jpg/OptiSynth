from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_change_case(organization_id: int, created_by_user_id: int, name: str,
                        trigger_type: str, restricted_substance: str,
                        qualification_spec_json: str) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO change_cases
                     (organization_id, created_by_user_id, name, trigger_type,
                      restricted_substance, qualification_spec_json, status,
                      created_at, updated_at)
                     VALUES (:org_id, :created_by, :name, :trigger_type,
                             :restricted_substance, :spec_json, 'draft',
                             :created_at, :updated_at) RETURNING id"""),
            {"org_id": organization_id, "created_by": created_by_user_id, "name": name,
             "trigger_type": trigger_type, "restricted_substance": restricted_substance,
             "spec_json": qualification_spec_json, "created_at": now_iso(), "updated_at": now_iso()},
        ).mappings().first()
        return row["id"]


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
    """Every diagnostic of the organization (newest first) with how much depends on it. Archived
    diagnostics are left out unless asked for."""
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
    """Updates only whitelisted columns of an org-owned diagnostic. The column names are never taken
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
    """Permanently deletes a diagnostic and everything under it, in ONE transaction, in explicit
    dependency order (so it behaves the same whether or not the database enforces foreign keys, and
    never leaves orphans). Returns the stored file names of its datasets so the caller can remove the
    files once the database change has committed -- or None if the diagnostic does not exist.
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
        conn.execute(text(f"DELETE FROM qualification_outcomes WHERE candidate_id IN ({candidate_ids})"), params)
        conn.execute(text(f"DELETE FROM predictions WHERE candidate_id IN ({candidate_ids})"), params)
        conn.execute(text(f"DELETE FROM recommended_experiments WHERE candidate_id IN ({candidate_ids})"), params)
        conn.execute(text("DELETE FROM candidate_substitutes WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text("DELETE FROM qualification_experiments WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text("DELETE FROM qualification_datasets WHERE organization_id = :org_id AND change_case_id = :case_id"), params)
        conn.execute(text("DELETE FROM change_cases WHERE id = :case_id AND organization_id = :org_id"), params)
        return stored_files
