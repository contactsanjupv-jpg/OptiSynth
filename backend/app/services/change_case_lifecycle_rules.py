"""
Pure rules for managing a program's lifecycle: what each status means, when it can be edited,
archived, restored or permanently deleted. No database / web-framework imports (same pattern as
change_case_rules.py), so every rule is directly unit-testable.

THE STATUS MODEL (the only meanings the product gives these words)
  draft      Setup in progress. Created, but nobody has started the program. The initial state.
  active     In progress. A person explicitly started the program ("Start program").
             Nothing sets this automatically: creating a case, uploading evidence, adding
             candidates, ranking or opening a case never changes the status.
  completed  Closed record. A person explicitly closed the program. It can never be reopened.
  archived   NOT a status. A separate marker (archived_at): the program is hidden from the default
             list and read-only, with everything retained. Any status can be archived and restored.

None of these means a material has passed physical testing: `active` is workflow progress, and a
ranked candidate is never "qualified" until a human records the physical outcome.

Transitions between draft -> active -> completed are the existing, unchanged rules in
change_case_rules.check_status_transition_allowed.
"""
from backend.app.schemas.errors import ValidationError

STATUS_MEANINGS = {
    "draft": "Setup in progress. The program has not been started.",
    "active": "In progress. A team member started the program. This is workflow progress, not a qualification result.",
    "completed": "Closed record. It cannot be reopened. Recorded physical outcomes are retained.",
}
ARCHIVED_MEANING = "Archived: hidden from the default list and read-only. Everything is retained and it can be restored."

NAME_MAX_LENGTH = 200
SUBSTANCE_MAX_LENGTH = 500
EDITABLE_FIELDS = {"name", "trigger_type", "restricted_substance", "target_value", "direction"}
REQUIREMENT_FIELDS = {"target_value", "direction"}
# What defines the evidence structure itself. Rejected with a pointer to creating a new program.
STRUCTURAL_SPEC_FIELDS = ("feature_columns", "target_metric", "units")


def is_archived(case: dict) -> bool:
    return bool(case.get("archived_at"))


def check_not_archived(case: dict) -> None:
    """Archived programs are read-only. Every write path calls this first."""
    if is_archived(case):
        raise ValidationError(
            "This program is archived and read-only. Restore it first if you need to change it.",
            field="archived",
        )


def check_can_archive(case: dict) -> None:
    if is_archived(case):
        raise ValidationError("This program is already archived.", field="archived")


def check_can_restore(case: dict) -> None:
    if not is_archived(case):
        raise ValidationError("This program is not archived.", field="archived")


def check_role_can_delete(role: str) -> None:
    """Permanent deletion is destructive, so it is limited to owners and admins. Anyone on the team
    can archive instead."""
    if role not in ("owner", "admin"):
        raise PermissionError(
            "Only an owner or admin can permanently delete a program. You can archive it instead."
        )


def delete_blocked_reason(case: dict, usage: dict):
    """None if the program may be permanently deleted, else a plain-language reason.

    Permanent deletion is for programs nothing depends on: setup that was abandoned or started in
    error. It is refused as soon as the program has produced anything a decision could rest on
    (a ranking) or recorded physical evidence (an outcome), and always for a completed (closed)
    record -- those are archived instead, never destroyed."""
    if case.get("status") == "completed":
        return "It is a completed (closed) record. Completed programs are retained; archive it instead."
    if usage.get("outcome_count", 0) > 0:
        return ("Physical validation outcomes have been recorded against its candidates. That evidence is "
                "retained; archive the program instead.")
    if usage.get("prediction_count", 0) > 0:
        return ("Candidates have been ranked, so results exist that a decision may have rested on. "
                "Archive the program instead of deleting it.")
    return None


def check_can_delete(case: dict, usage: dict) -> None:
    reason = delete_blocked_reason(case, usage)
    if reason:
        raise ValidationError(f"This program can't be permanently deleted. {reason}", field="delete")


def requirement_locked_reason(case: dict, usage: dict):
    """None if the requirement (threshold / direction) may still be changed, else why not.
    Changing it after rankings exist would leave stored probabilities that were computed against a
    different requirement displayed as if they described the current one."""
    if is_archived(case):
        return "The program is archived."
    if case.get("status") == "completed":
        return "The program is completed (a closed record)."
    if usage.get("prediction_count", 0) > 0 or usage.get("outcome_count", 0) > 0:
        return ("Candidates have already been ranked against this requirement, so it can no longer be changed. "
                "Create a new program for a different requirement.")
    return None


def clean_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("A program needs a name.", field="name")
    value = value.strip()
    if len(value) > NAME_MAX_LENGTH:
        raise ValidationError(f"The name is too long (max {NAME_MAX_LENGTH} characters).", field="name")
    return value


def clean_substance(value):
    """Empty / blank -> None (cleared)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("The restricted substance must be text.", field="restricted_substance")
    value = value.strip()
    if len(value) > SUBSTANCE_MAX_LENGTH:
        raise ValidationError(
            f"The restricted substance is too long (max {SUBSTANCE_MAX_LENGTH} characters).",
            field="restricted_substance",
        )
    return value or None


def check_can_edit(case: dict, usage: dict, changes: dict) -> None:
    """changes: only the keys the caller wants to change. Raises ValidationError if the edit is not
    permitted right now."""
    if not changes:
        raise ValidationError("Nothing to change.", field="changes")
    unknown = sorted(set(changes) - EDITABLE_FIELDS)
    if unknown:
        raise ValidationError("These fields can't be edited: " + ", ".join(unknown) + ".", field="changes")
    check_not_archived(case)
    if case.get("status") == "completed":
        raise ValidationError(
            "A completed program is a closed record and can't be edited.", field="status",
        )
    if REQUIREMENT_FIELDS & set(changes):
        reason = requirement_locked_reason(case, usage)
        if reason:
            raise ValidationError(f"The requirement can't be changed. {reason}", field="requirement")