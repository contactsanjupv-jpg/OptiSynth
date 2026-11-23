from datetime import datetime, timezone

from backend.app.repositories import change_case_repo, notifications_repo, program_workspace_repo as repo
from backend.app.schemas.errors import Forbidden, ValidationError
from backend.app.services import change_case_lifecycle_rules as lifecycle_rules
from backend.app.services import program_stage_rules as stage_rules
from backend.app.services import program_workspace_rules as rules

MANAGER_MESSAGE = "Only an owner, an admin or the program owner can do this."


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _person(name, email, user_id=None):
    label = name or email or None
    return {"id": user_id, "name": label} if user_id is not None or label else None


def _case(organization_id: int, case_id: int) -> dict:
    case = change_case_repo.get_change_case(organization_id, case_id)
    if case is None:
        raise LookupError("Program not found.")
    return case


def _writable(organization_id: int, case_id: int, closed_ok: bool = False) -> dict:
    case = _case(organization_id, case_id)
    lifecycle_rules.check_not_archived(case)
    if not closed_ok and case["status"] == "completed":
        raise ValidationError("This program is closed and can no longer be changed.", field="status")
    return case


def _can_manage(organization_id: int, role: str, user_id: int, case: dict) -> bool:
    return role in rules.DECISION_ROLES or case.get("owner_user_id") == user_id


def _require_manager(organization_id: int, role: str, user_id: int, case: dict) -> None:
    if not _can_manage(organization_id, role, user_id, case):
        raise Forbidden(MANAGER_MESSAGE)


def _member_or_error(organization_id: int, user_id, field: str) -> int:
    if not isinstance(user_id, int) or isinstance(user_id, bool) or not repo.is_active_member(organization_id, user_id):
        raise ValidationError("Choose a person from your organization.", field=field)
    return user_id


def _notify(organization_id: int, actor_id: int, user_id, case_id: int, kind: str, title: str, body) -> None:
    if user_id and user_id != actor_id:
        notifications_repo.create_many(organization_id, [user_id], kind, title, body, f"/programs/{case_id}")


# ------------------------------------------------------------------ the whole workspace
def _support(req: dict, facts: dict) -> str:
    if req["is_ranking_target"]:
        if facts["passed_outcome_count"] > 0:
            return "met"
        return "not_met" if facts["outcome_count"] > 0 else "no_result"
    latest = facts["latest_met"].get(req["id"])
    return "no_result" if latest is None else ("met" if latest else "not_met")


def completion_blockers(organization_id: int, case: dict) -> dict:
    facts = repo.support_facts(organization_id, case["id"])
    missing = rules.unsupported_critical(facts["requirements"], facts["passed_outcome_count"], facts["latest_met"])
    has_critical = any(r["criticality"] == "critical" for r in facts["requirements"])
    return {"missing_critical": missing, "message": rules.completion_problem(has_critical, missing, case["stage"]),
            "has_critical": has_critical}


def check_can_complete(organization_id: int, case_id: int) -> None:
    """Called before a program's status becomes completed."""
    problem = completion_blockers(organization_id, _case(organization_id, case_id))["message"]
    if problem:
        raise ValidationError(problem, field="status")


def unsupported_critical_names(organization_id: int, case_id: int) -> list:
    facts = repo.support_facts(organization_id, case_id)
    return rules.unsupported_critical(facts["requirements"], facts["passed_outcome_count"], facts["latest_met"])


def workspace(organization_id: int, role: str, user_id: int, case_id: int) -> dict:
    case = _case(organization_id, case_id)
    facts = repo.support_facts(organization_id, case_id)
    archived_requirements = [
        {k: r[k] for k in ("id", "property", "threshold", "direction", "unit", "test_method", "criticality", "source", "archived_at")}
        for r in repo.list_requirements(organization_id, case_id, include_archived=True) if r["archived_at"]]
    by_id = {p["id"]: p for p in repo.people(organization_id)}
    results = repo.list_results(organization_id, case_id)
    latest = {}
    for r in results:  # newest first: the first one seen per requirement is the latest
        latest.setdefault(r["requirement_id"], r)
    counts = {}
    for r in results:
        counts[r["requirement_id"]] = counts.get(r["requirement_id"], 0) + 1
    requirements = []
    for r in facts["requirements"]:
        last = latest.get(r["id"])
        requirements.append({
            **{k: r[k] for k in ("id", "property", "threshold", "direction", "unit", "test_method", "criticality", "source",
                                 "is_ranking_target", "created_at", "updated_at")},
            "is_ranking_target": bool(r["is_ranking_target"]), "support": _support(r, facts),
            "result_count": counts.get(r["id"], 0),
            "latest_result": None if not last else {"id": last["id"], "text": last["result_text"], "met": bool(last["met"]),
                                                    "reference": last["test_reference"], "created_at": last["created_at"],
                                                    "recorded_by": last["actor_name"] or last["actor_email"]},
        })
    now = today()
    owner = by_id.get(case.get("owner_user_id"))
    owner_row = {"id": owner["id"], "name": owner["display_name"] or owner["email"]} if owner else None
    reviewers = [{"id": r["user_id"], "name": r["display_name"] or r["email"]} for r in repo.list_reviewers(organization_id, case_id)]
    is_owner = case.get("owner_user_id") == user_id
    closed, archived = case["status"] == "completed", bool(case.get("archived_at"))
    blockers = completion_blockers(organization_id, case)
    return {
        "program": {"id": case["id"], "name": case["name"], "status": case["status"], "stage": case["stage"],
                    "stage_label": stage_rules.label(case["stage"]), "trigger_type": case["trigger_type"],
                    "restricted_substance": case["restricted_substance"], "target_date": case.get("target_date"),
                    "created_at": case["created_at"], "updated_at": case["updated_at"], "archived": archived,
                    **{f: case.get(f) for f in rules.DETAIL_FIELDS}},
        "owner": owner_row, "reviewers": reviewers, "requirements": requirements, "archived_requirements": archived_requirements,
        "archived_requirements": archived_requirements,
        "milestones": [{**m, "overdue": rules.is_overdue(m["due_date"], m["completed_at"], now)} for m in repo.list_milestones(organization_id, case_id)],
        "tasks": [{"id": t["id"], "title": t["title"], "due_date": t["due_date"], "completed_at": t["completed_at"],
                   "assignee": _person(t["assignee_name"], t["assignee_email"], t["assignee_user_id"]),
                   "overdue": rules.is_overdue(t["due_date"], t["completed_at"], now)} for t in repo.list_tasks(organization_id, case_id)],
        "completion": blockers,
        "permissions": {"can_manage": _can_manage(organization_id, role, user_id, case) and not archived and not closed,
                        "can_write": not archived and not closed,
                        "can_decide": not archived and (role in rules.DECISION_ROLES or is_owner
                                                        or repo.is_reviewer(organization_id, case_id, user_id))},
        "people": [{"id": p["id"], "name": p["display_name"] or p["email"], "role": p["role"]} for p in by_id.values()],
        "today": now,
    }


# ------------------------------------------------------------------ details, owner, reviewers
def update_details(organization_id: int, role: str, user_id: int, case_id: int, changes: dict) -> list:
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    if not changes:
        raise ValidationError("Nothing to change.", field="changes")
    fields = {}
    for key, value in changes.items():
        fields[key] = rules.clean_date(value, key) if key == "target_date" else rules.clean_detail(key, value)
    repo.update_details(organization_id, case_id, fields)
    return sorted(fields)


def set_owner(organization_id: int, role: str, user_id: int, case_id: int, new_owner) -> None:
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    if new_owner is not None:
        _member_or_error(organization_id, new_owner, "user_id")
    repo.set_owner(organization_id, case_id, new_owner)
    _notify(organization_id, user_id, new_owner, case_id, "assigned", "You are the program owner", case["name"])


def add_reviewer(organization_id: int, role: str, user_id: int, case_id: int, reviewer_id: int) -> None:
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    _member_or_error(organization_id, reviewer_id, "user_id")
    if not repo.add_reviewer(organization_id, case_id, reviewer_id, user_id):
        raise ValidationError("This person is already a reviewer.", field="user_id")
    _notify(organization_id, user_id, reviewer_id, case_id, "assigned", "You were added as a reviewer", case["name"])


def remove_reviewer(organization_id: int, role: str, user_id: int, case_id: int, reviewer_id: int) -> None:
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    if not repo.remove_reviewer(organization_id, case_id, reviewer_id):
        raise LookupError("Reviewer not found.")


# ------------------------------------------------------------------ requirements
def add_requirement(organization_id: int, role: str, user_id: int, case_id: int, data: dict) -> int:
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    fields = rules.clean_requirement(data)
    active = repo.list_requirements(organization_id, case_id)
    if any(r["property"].lower() == fields["property"].lower() and (r["unit"] or "") == (fields.get("unit") or "") for r in active):
        raise ValidationError("This program already has a requirement for that property and unit.", field="property")
    if len(active) >= 100:
        raise ValidationError("A program can hold up to 100 requirements.", field="property")
    return repo.create_requirement(organization_id, case_id, user_id, fields)


def _requirement_or_404(organization_id: int, case_id: int, requirement_id: int) -> dict:
    req = repo.get_requirement(organization_id, case_id, requirement_id)
    if req is None:
        raise LookupError("Requirement not found.")
    return req


def update_requirement(organization_id: int, role: str, user_id: int, case_id: int, requirement_id: int, changes: dict) -> list:
    from backend.app.services import change_case_lifecycle_service
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    req = _requirement_or_404(organization_id, case_id, requirement_id)
    if req["archived_at"]:
        raise ValidationError("This requirement is archived. Restore it before changing it.", field="requirement")
    if not changes:
        raise ValidationError("Nothing to change.", field="changes")
    fields = rules.clean_requirement(changes, partial=True)
    if req["criticality"] == "critical" and fields.get("criticality", "critical") != "critical":
        facts = repo.support_facts(organization_id, case_id)
        if _support(req, facts) != "met":
            raise ValidationError("This critical requirement has no recorded result that met it, so it cannot be made less critical. "
                                  "Record a result that met it first.", field="criticality")
    measured = {"property", "threshold", "direction", "unit"} & set(fields)
    if req["is_ranking_target"]:
        if {"property", "unit"} & set(fields):
            raise ValidationError("The property and unit of the ranking target are part of the program definition and cannot be changed.",
                                  field="property")
        spec_changes = {}
        if "threshold" in fields and fields["threshold"] != req["threshold"]:
            spec_changes["target_value"] = fields["threshold"]
        if "direction" in fields and fields["direction"] != req["direction"]:
            spec_changes["direction"] = fields["direction"]
        if spec_changes:
            change_case_lifecycle_service.update_case(organization_id, case_id, spec_changes)  # enforces the lock, syncs the register
        fields = {k: v for k, v in fields.items() if k not in ("threshold", "direction")}
    elif measured and any(r["requirement_id"] == requirement_id for r in repo.list_results(organization_id, case_id)):
        changed = [k for k in measured if fields[k] != req[k]]
        if changed:
            raise ValidationError("Results are recorded against this requirement, so its property, threshold, direction and unit "
                                  "can no longer be changed. Archive it and add a new requirement instead.", field="threshold")
    if fields:
        repo.update_requirement(organization_id, requirement_id, fields)
    return sorted(changes)


def archive_requirement(organization_id: int, role: str, user_id: int, case_id: int, requirement_id: int, archived: bool) -> None:
    case = _writable(organization_id, case_id)
    _require_manager(organization_id, role, user_id, case)
    req = _requirement_or_404(organization_id, case_id, requirement_id)
    if req["is_ranking_target"]:
        raise ValidationError("The ranking target is part of the program definition and cannot be archived.", field="requirement")
    if archived and req["criticality"] == "critical":
        raise ValidationError("A critical requirement cannot be archived. Archiving would remove an outstanding qualification obligation. "
                              "Once a recorded result has met it, change its criticality first.", field="requirement")
    if archived == bool(req["archived_at"]):
        raise ValidationError("This requirement is already archived." if archived else "This requirement is not archived.", field="requirement")
    if not archived:
        key = (req["property"].lower(), req["unit"] or "")
        if any((r["property"].lower(), r["unit"] or "") == key for r in repo.list_requirements(organization_id, case_id)):
            raise ValidationError("An active requirement for that property and unit already exists, so this one cannot be restored.",
                                  field="requirement")
    repo.set_requirement_archived(organization_id, requirement_id, archived)


def record_result(organization_id: int, user_id: int, case_id: int, requirement_id: int, data: dict) -> int:
    _writable(organization_id, case_id)
    req = _requirement_or_404(organization_id, case_id, requirement_id)
    if req["archived_at"]:
        raise ValidationError("This requirement is archived.", field="requirement")
    if req["is_ranking_target"]:
        raise ValidationError("Record physical results for this requirement on the candidates. They are used automatically.", field="requirement")
    text_value = rules.clean_text(data.get("result_text"), "result_text", 1000, "The result", required=True)
    reference = rules.clean_text(data.get("test_reference"), "test_reference", 300, "The test reference")
    if not isinstance(data.get("met"), bool):
        raise ValidationError("Say whether the result met the requirement.", field="met")
    return repo.add_result(organization_id, requirement_id, user_id, text_value, data["met"], reference)


# ------------------------------------------------------------------ milestones and tasks
def add_milestone(organization_id: int, user_id: int, case_id: int, title, due_date) -> int:
    _writable(organization_id, case_id)
    return repo.create_milestone(organization_id, case_id, user_id, rules.clean_title(title, "milestone name"),
                                 rules.clean_date(due_date, "due_date", required=True))


def update_milestone(organization_id: int, user_id: int, case_id: int, milestone_id: int, changes: dict) -> list:
    _writable(organization_id, case_id)
    milestone = repo.get_milestone(organization_id, case_id, milestone_id)
    if milestone is None:
        raise LookupError("Milestone not found.")
    if not changes:
        raise ValidationError("Nothing to change.", field="changes")
    fields = {}
    if "title" in changes:
        fields["title"] = rules.clean_title(changes["title"], "milestone name")
    if "due_date" in changes:
        fields["due_date"] = rules.clean_date(changes["due_date"], "due_date", required=True)
    if "completed" in changes:
        if changes["completed"] is None:
            raise ValidationError("Say whether the milestone is complete.", field="completed")
        done = bool(changes["completed"])
        fields["completed_at"] = datetime.now(timezone.utc).isoformat() if done else None
        fields["completed_by_user_id"] = user_id if done else None
    repo.update_milestone(organization_id, milestone_id, fields)
    return sorted(changes)


def delete_milestone(organization_id: int, case_id: int, milestone_id: int) -> None:
    _writable(organization_id, case_id)
    if repo.get_milestone(organization_id, case_id, milestone_id) is None:
        raise LookupError("Milestone not found.")
    repo.delete_milestone(organization_id, milestone_id)


def add_task(organization_id: int, user_id: int, case_id: int, title, assignee, due_date) -> int:
    case = _writable(organization_id, case_id)
    if assignee is not None:
        _member_or_error(organization_id, assignee, "assignee_user_id")
    task_id = repo.create_task(organization_id, case_id, user_id, rules.clean_title(title, "task name"), assignee,
                               rules.clean_date(due_date, "due_date"))
    _notify(organization_id, user_id, assignee, case_id, "assigned", "A review task was assigned to you", case["name"])
    return task_id


def set_task_done(organization_id: int, user_id: int, case_id: int, task_id: int, done: bool) -> None:
    _writable(organization_id, case_id)
    if repo.get_task(organization_id, case_id, task_id) is None:
        raise LookupError("Task not found.")
    repo.set_task_done(organization_id, task_id, user_id, done)


def delete_task(organization_id: int, case_id: int, task_id: int) -> None:
    _writable(organization_id, case_id)
    if repo.get_task(organization_id, case_id, task_id) is None:
        raise LookupError("Task not found.")
    repo.delete_task(organization_id, task_id)


# ------------------------------------------------------------------ comments, decisions, timeline
def list_comments(organization_id: int, case_id: int) -> list:
    _case(organization_id, case_id)
    return [{"id": c["id"], "kind": c["kind"], "body": c["body"], "created_at": c["created_at"],
             "author": c["actor_name"] or c["actor_email"]} for c in repo.list_comments(organization_id, case_id)]


def add_comment(organization_id: int, role: str, user_id: int, case_id: int, kind, body) -> tuple:
    case = _writable(organization_id, case_id, closed_ok=True)  # discussion stays open on a closed program
    kind, body = rules.clean_comment(kind, body)
    if kind == "decision":
        rules.check_can_record_decision(role, case.get("owner_user_id") == user_id, repo.is_reviewer(organization_id, case_id, user_id))
    return repo.add_comment(organization_id, case_id, user_id, kind, body), kind


def timeline(organization_id: int, case_id: int) -> list:
    from backend.app.services import portfolio_service
    _case(organization_id, case_id)
    labels = {**portfolio_service.ACTIVITY_LABELS, **ACTIVITY_LABELS}
    out = []
    for a in repo.timeline(organization_id, case_id):
        out.append({"id": a["id"], "label": labels.get(a["action"], a["action"].replace("_", " ").replace(".", " ").capitalize()),
                    "actor": a["actor_name"] or a["actor_email"], "created_at": a["created_at"]})
    return out


ACTIVITY_LABELS = {
    "change_case.details_update": "Substitution details updated", "change_case.owner_set": "Program owner changed",
    "change_case.reviewer_add": "Reviewer added", "change_case.reviewer_remove": "Reviewer removed",
    "change_case.requirement_add": "Requirement added", "change_case.requirement_update": "Requirement updated",
    "change_case.requirement_archive": "Requirement archived", "change_case.requirement_restore": "Requirement restored",
    "change_case.requirement_result": "Requirement result recorded", "change_case.milestone_add": "Milestone added",
    "change_case.milestone_update": "Milestone updated", "change_case.milestone_delete": "Milestone removed",
    "change_case.task_add": "Review task added", "change_case.task_update": "Review task updated",
    "change_case.task_delete": "Review task removed", "change_case.comment_add": "Comment added",
    "change_case.decision_record": "Decision recorded", "change_case.evidence_metadata": "Evidence details updated",
    "change_case.evidence_review": "Evidence reviewed", "change_case.conflicts_scan": "Evidence conflicts checked",
    "change_case.conflict_resolve": "Evidence conflict resolved",
}
