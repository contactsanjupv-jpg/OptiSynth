"""
Managing a diagnostic after it exists: viewing it with what depends on it, editing it, archiving /
restoring it, and permanently deleting it when -- and only when -- nothing depends on it.

Every function is organization-scoped through the repository (a diagnostic of another organization is
indistinguishable from a missing one: LookupError -> 404). The rules live in
change_case_lifecycle_rules.py.
"""
import json
import os
from typing import get_args

from backend.app.repositories import change_case_repo
from backend.app.schemas.change_case_schemas import TriggerType, validate_qualification_spec
from backend.app.schemas.errors import ValidationError
from backend.app.services import change_case_lifecycle_rules as rules
from backend.app.services import qualification_dataset_service

_USAGE_KEYS = ("dataset_count", "candidate_count", "prediction_count", "outcome_count")


def _usage(row: dict) -> dict:
    return {k: row.get(k, 0) or 0 for k in _USAGE_KEYS}


def describe(row: dict) -> dict:
    """The diagnostic as the API returns it: the stored record plus what depends on it and what may
    be done with it. Nothing here is persisted -- it is derived on every read, so it can never drift
    from the real data."""
    usage = _usage(row)
    blocked = rules.delete_blocked_reason(row, usage)
    locked = rules.requirement_locked_reason(row, usage)
    out = {k: v for k, v in row.items() if k not in _USAGE_KEYS}
    out.update(usage)
    out.update({
        "archived": rules.is_archived(row),
        "status_meaning": rules.ARCHIVED_MEANING if rules.is_archived(row) else rules.STATUS_MEANINGS.get(row["status"]),
        "deletable": blocked is None,
        "delete_blocked_reason": blocked,
        "requirement_editable": locked is None,
        "requirement_locked_reason": locked,
    })
    return out


def list_cases(organization_id: int, include_archived: bool = False) -> list:
    return [describe(r) for r in change_case_repo.list_change_cases_with_usage(organization_id, include_archived)]


def get_case_or_404(organization_id: int, change_case_id: int) -> dict:
    row = change_case_repo.get_change_case_with_usage(organization_id, change_case_id)
    if row is None:
        raise LookupError("Change case not found.")
    return describe(row)


def update_case(organization_id: int, change_case_id: int, changes: dict) -> dict:
    row = change_case_repo.get_change_case_with_usage(organization_id, change_case_id)
    if row is None:
        raise LookupError("Change case not found.")
    rules.check_can_edit(row, _usage(row), changes)

    fields = {}
    if "name" in changes:
        fields["name"] = rules.clean_name(changes["name"])
    if "trigger_type" in changes:
        if changes["trigger_type"] not in get_args(TriggerType):  # same value set the API schema uses
            raise ValidationError("Unknown change reason.", field="trigger_type")
        fields["trigger_type"] = changes["trigger_type"]
    if "restricted_substance" in changes:
        fields["restricted_substance"] = rules.clean_substance(changes["restricted_substance"])
    if rules.REQUIREMENT_FIELDS & set(changes):
        spec = json.loads(row["qualification_spec_json"])
        for key in rules.REQUIREMENT_FIELDS & set(changes):
            spec[key] = changes[key]
        # the whole spec goes back through the strict validator (finite number, valid direction, ...)
        fields["qualification_spec_json"] = json.dumps(validate_qualification_spec(spec))

    change_case_repo.update_change_case_fields(organization_id, change_case_id, fields)
    return get_case_or_404(organization_id, change_case_id)


def archive_case(organization_id: int, change_case_id: int) -> dict:
    row = change_case_repo.get_change_case_with_usage(organization_id, change_case_id)
    if row is None:
        raise LookupError("Change case not found.")
    rules.check_can_archive(row)
    change_case_repo.set_archived(organization_id, change_case_id, True)
    return get_case_or_404(organization_id, change_case_id)


def restore_case(organization_id: int, change_case_id: int) -> dict:
    row = change_case_repo.get_change_case_with_usage(organization_id, change_case_id)
    if row is None:
        raise LookupError("Change case not found.")
    rules.check_can_restore(row)
    change_case_repo.set_archived(organization_id, change_case_id, False)
    return get_case_or_404(organization_id, change_case_id)


def delete_case(organization_id: int, actor_role: str, change_case_id: int) -> dict:
    """Permanent deletion. Refused (with the reason, and archive as the alternative) whenever anything
    depends on the diagnostic. Returns a summary of what was removed."""
    rules.check_role_can_delete(actor_role)
    row = change_case_repo.get_change_case_with_usage(organization_id, change_case_id)
    if row is None:
        raise LookupError("Change case not found.")
    usage = _usage(row)
    rules.check_can_delete(row, usage)

    stored_files = change_case_repo.delete_change_case_tree(organization_id, change_case_id)
    if stored_files is None:
        raise LookupError("Change case not found.")
    # Only after the database change has committed: remove the uploaded originals. A file that cannot
    # be removed is reported, never allowed to undo or fail the (already committed) deletion.
    folder = os.path.join(qualification_dataset_service.UPLOAD_ROOT, str(organization_id))
    files_removed, files_failed = 0, 0
    for name in stored_files:
        if not name or os.path.basename(name) != name:  # never follow a path out of the org folder
            files_failed += 1
            continue
        try:
            os.remove(os.path.join(folder, name))
            files_removed += 1
        except FileNotFoundError:
            files_removed += 1
        except OSError:
            files_failed += 1
    return {"deleted": True, "id": change_case_id, "name": row["name"], "datasets_removed": usage["dataset_count"],
            "candidates_removed": usage["candidate_count"], "files_removed": files_removed,
            "files_not_removed": files_failed}