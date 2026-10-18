"""
Managing a candidate substitute after it exists: editing its name and input values, removing it from
the analysis (archive) and restoring it, and permanently deleting it when -- and only when -- nothing
depends on it. The rules live in candidate_lifecycle_rules.py.

Every function is organization-scoped: the diagnostic is looked up through the repository with the
caller's organization, and the candidate must belong to THAT diagnostic. Anything else (missing,
another organization's, another diagnostic's) is the same LookupError -> 404, so the response never
reveals whether an id exists elsewhere.

Callers (the routes) have already refused an archived diagnostic (change_case_routes._writable_case).
What these functions return is the candidate in exactly the shape GET .../candidates returns, so the
screen can replace its copy without a second request.
"""
import json

from backend.app.repositories import candidate_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import candidate_lifecycle_rules as rules
from backend.app.services import change_case_rules, change_case_service, evidence_rules


def _case_and_candidate(organization_id: int, change_case_id: int, candidate_id: int):
    case = change_case_service.get_change_case_or_404(organization_id, change_case_id)
    candidate = candidate_repo.get_candidate(organization_id, candidate_id)
    if candidate is None or candidate["change_case_id"] != change_case_id:
        raise LookupError("Candidate not found.")
    return case, candidate


def _view(organization_id: int, change_case_id: int, candidate_id: int) -> dict:
    for c in change_case_service.list_candidates_with_predictions(
        organization_id, change_case_id, include_archived=True,
    ):
        if c["id"] == candidate_id:
            return c
    raise LookupError("Candidate not found.")


def _usage(organization_id: int, change_case_id: int, candidate_id: int) -> dict:
    return candidate_repo.get_candidate_usage_map(organization_id, change_case_id).get(candidate_id, {})


def update_candidate(organization_id: int, change_case_id: int, candidate_id: int, changes: dict) -> dict:
    """Edit a candidate's name and/or its input values. Returns (candidate view, what changed) via the
    view plus the `_changed` marker the route uses for the audit entry."""
    case, candidate = _case_and_candidate(organization_id, change_case_id, candidate_id)
    rules.check_can_edit(case, candidate, changes)
    usage = _usage(organization_id, change_case_id, candidate_id)

    fields = {}
    changed = []
    bump = False

    if "name" in changes:
        name = rules.clean_name(changes["name"])
        if name != candidate["candidate_name"]:
            others = [
                c["candidate_name"]
                for c in candidate_repo.list_candidates_for_change_case(
                    organization_id, change_case_id, include_archived=True)
                if c["id"] != candidate_id
            ]
            change_case_rules.check_candidate_name_unique(name, others)
            fields["candidate_name"] = name
            changed.append("name")

    if "features" in changes:
        spec = json.loads(case["qualification_spec_json"])
        features = changes["features"]
        units = changes.get("units")
        label = fields.get("candidate_name") or candidate["candidate_name"]
        # Exactly the checks a new candidate gets -- an edit can never store what an add would refuse.
        change_case_rules.check_candidate_features_complete(spec.get("feature_columns"), features, label)
        rules.check_features_known(spec.get("feature_columns"), features, label)
        change_case_rules.check_candidate_features_finite(features, label)
        converted, input_record, problems = evidence_rules.convert_candidate_inputs(spec, features, units)
        if problems:
            raise ValidationError(f"Candidate {label!r} was not changed: " + " ".join(problems), field="features")
        change_case_rules.check_candidate_features_finite(converted, label)
        new_input_json = json.dumps(input_record) if (units or spec.get("units")) else None

        old_properties = json.loads(candidate["properties_json"])
        old_input = json.loads(candidate["input_json"]) if candidate.get("input_json") else None
        new_input = json.loads(new_input_json) if new_input_json else None
        if converted != old_properties or new_input != old_input:
            rules.check_features_editable(candidate, usage)
            fields["properties_json"] = json.dumps(converted)
            fields["input_json"] = new_input_json
            changed.append("features")
            bump = True

    if not fields:
        raise ValidationError("Nothing has changed.", field="changes")

    candidate_repo.update_candidate_fields(organization_id, candidate_id, fields, bump_inputs_version=bump)
    view = _view(organization_id, change_case_id, candidate_id)
    view["_changed"] = changed
    view["_previous_features"] = json.loads(candidate["properties_json"]) if bump else None
    return view


def archive_candidate(organization_id: int, change_case_id: int, candidate_id: int) -> dict:
    """Remove a candidate from the analysis. Nothing is deleted: its predictions, recommended
    experiments and recorded outcomes are all kept, and it can be restored."""
    case, candidate = _case_and_candidate(organization_id, change_case_id, candidate_id)
    rules.check_case_open(case)
    rules.check_can_archive(candidate)
    candidate_repo.set_candidate_archived(organization_id, candidate_id, True)
    return _view(organization_id, change_case_id, candidate_id)


def restore_candidate(organization_id: int, change_case_id: int, candidate_id: int) -> dict:
    case, candidate = _case_and_candidate(organization_id, change_case_id, candidate_id)
    rules.check_case_open(case)
    rules.check_can_restore(candidate)
    candidate_repo.set_candidate_archived(organization_id, candidate_id, False)
    return _view(organization_id, change_case_id, candidate_id)


def delete_candidate(organization_id: int, change_case_id: int, candidate_id: int) -> dict:
    """Permanent deletion of a candidate nothing depends on. Refused -- with the reason, and removing
    it from the analysis as the alternative -- once it has been ranked or has a recorded outcome."""
    case, candidate = _case_and_candidate(organization_id, change_case_id, candidate_id)
    rules.check_case_open(case)
    rules.check_can_delete(candidate, _usage(organization_id, change_case_id, candidate_id))
    try:
        deleted = candidate_repo.delete_candidate(organization_id, candidate_id)
    except candidate_repo.CandidateHasDependents:
        # Something was recorded between the check above and the delete: refuse, delete nothing.
        raise ValidationError(
            "This candidate can't be permanently deleted: results were recorded for it a moment ago. "
            "Remove it from the analysis instead.", field="delete",
        ) from None
    if not deleted:
        raise LookupError("Candidate not found.")
    return {"deleted": True, "id": candidate_id, "name": candidate["candidate_name"]}