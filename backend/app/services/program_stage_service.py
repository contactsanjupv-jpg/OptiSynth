from backend.app.repositories import program_stage_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import program_stage_rules as rules


def _facts_or_404(organization_id: int, change_case_id: int) -> dict:
    facts = program_stage_repo.program_facts(organization_id, change_case_id)
    if not facts:
        raise LookupError("Program not found.")
    return facts


def stage_options(organization_id: int, role: str, change_case_id: int) -> dict:
    """The current stage plus every stage the program may move to now, each with the reason it is blocked (if any)."""
    facts = _facts_or_404(organization_id, change_case_id)
    current = facts["stage"]
    options = []
    for target in rules.allowed_targets(current):
        try:
            rules.check_transition(current, target, facts["status"], facts["archived_at"] is not None, role,
                                   "reason" if (rules.is_backward(current, target) or target in rules.DECISIONS) else "", facts)
            blocked = None
        except ValidationError as err:
            blocked = err.message
        options.append({"stage": target, "label": rules.label(target), "blocked_reason": blocked,
                        "needs_reason": rules.is_backward(current, target) or target in rules.DECISIONS})
    return {"stage": current, "label": rules.label(current), "options": options}


def change_stage(organization_id: int, role: str, actor_user_id: int, change_case_id: int, to_stage: str, reason) -> dict:
    facts = _facts_or_404(organization_id, change_case_id)
    current = facts["stage"]
    cleaned = rules.check_transition(current, to_stage, facts["status"], facts["archived_at"] is not None, role, reason, facts)
    if not program_stage_repo.set_stage(organization_id, change_case_id, current, to_stage, cleaned, actor_user_id):
        raise ValidationError("The stage was changed by someone else. Reload and try again.", field="stage")
    from backend.app.services import notification_service
    notification_service.notify_stage_change(organization_id, actor_user_id, change_case_id, to_stage, cleaned)
    return {"change_case_id": change_case_id, "from_stage": current, "stage": to_stage, "label": rules.label(to_stage)}


def close_with_status(organization_id: int, actor_user_id: int, change_case_id: int) -> None:
    """Called when a program's status becomes completed: the stage follows, with a history row."""
    facts = program_stage_repo.program_facts(organization_id, change_case_id)
    if facts and facts["stage"] != rules.CLOSED:
        program_stage_repo.set_stage(organization_id, change_case_id, facts["stage"], rules.CLOSED,
                                     "Program closed", actor_user_id)


def stage_history(organization_id: int, change_case_id: int) -> list:
    _facts_or_404(organization_id, change_case_id)
    rows = program_stage_repo.history(organization_id, change_case_id)
    for r in rows:
        r["from_label"] = rules.label(r["from_stage"]) if r["from_stage"] else None
        r["to_label"] = rules.label(r["to_stage"])
        r["actor"] = r.pop("actor_name") or r.pop("actor_email") or None
        r.pop("actor_email", None)
    return rows
