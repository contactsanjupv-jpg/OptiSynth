"""Rules of the program workspace: requirements, milestones, tasks, comments and the completion gate. Pure: no database access.

A requirement is supported only by a recorded physical result that met it. A model ranking never supports a requirement,
and a requirement with no result is never treated as passing."""
import math
from datetime import date

from backend.app.schemas.errors import ValidationError

CRITICALITIES = ("critical", "important", "informational")
CRITICALITY_LABELS = {"critical": "Critical", "important": "Important", "informational": "Informational"}
DIRECTIONS = ("maximize", "minimize")
COMMENT_KINDS = ("comment", "decision")
DECISION_ROLES = ("owner", "admin")

DETAIL_FIELDS = {"material_replaced": 300, "reason_for_substitution": 2000, "business_impact": 2000,
                 "original_formulation": 2000, "manufacturing_constraints": 2000}
DETAIL_LABELS = {"material_replaced": "The material being replaced", "reason_for_substitution": "The reason",
                 "business_impact": "The business impact", "original_formulation": "The original formulation",
                 "manufacturing_constraints": "The manufacturing constraints"}


def clean_text(value, field: str, limit: int, label: str, required: bool = False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError(f"{label} is required.", field=field)
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text.", field=field)
    value = value.strip()
    if len(value) > limit:
        raise ValidationError(f"{label} is too long. Keep it under {limit} characters.", field=field)
    return value


def clean_detail(field: str, value):
    return clean_text(value, field, DETAIL_FIELDS[field], DETAIL_LABELS[field])


def clean_date(value, field: str, required: bool = False):
    """A calendar date as YYYY-MM-DD."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValidationError("Choose a date.", field=field)
        return None
    try:
        parsed = date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError("Use the date format YYYY-MM-DD.", field=field) from None
    if parsed.year < 2000 or parsed.year > 2100:
        raise ValidationError("Choose a date between 2000 and 2100.", field=field)
    return parsed.isoformat()


def clean_requirement(data: dict, partial: bool = False) -> dict:
    """Validated requirement fields. With partial=True only the keys present are checked."""
    out = {}
    if not partial or "property" in data:
        out["property"] = clean_text(data.get("property"), "property", 120, "The property", required=True)
    if not partial or "threshold" in data:
        value = data.get("threshold")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValidationError("The threshold must be a finite number.", field="threshold")
        out["threshold"] = float(value)
    if not partial or "direction" in data:
        if data.get("direction") not in DIRECTIONS:
            raise ValidationError("Choose whether the value must be at least or at most the threshold.", field="direction")
        out["direction"] = data["direction"]
    if not partial or "unit" in data:
        out["unit"] = clean_text(data.get("unit"), "unit", 40, "The unit")
    if not partial or "test_method" in data:
        out["test_method"] = clean_text(data.get("test_method"), "test_method", 200, "The test method")
    if not partial or "source" in data:
        out["source"] = clean_text(data.get("source"), "source", 200, "The source")
    if not partial or "criticality" in data:
        crit = data.get("criticality", "important")
        if crit not in CRITICALITIES:
            raise ValidationError("Choose critical, important or informational.", field="criticality")
        out["criticality"] = crit
    return out


def clean_title(value, what: str = "title") -> str:
    return clean_text(value, "title", 200, f"The {what}", required=True)


def clean_comment(kind, body):
    if kind not in COMMENT_KINDS:
        raise ValidationError("Choose a comment or a decision.", field="kind")
    return kind, clean_text(body, "body", 4000, "The text", required=True)


def check_can_record_decision(role: str, is_program_owner: bool, is_reviewer: bool) -> None:
    if role not in DECISION_ROLES and not is_program_owner and not is_reviewer:
        raise ValidationError("Only an owner, an admin, the program owner or a reviewer can record a decision.", field="kind")


def requirement_supported(req: dict, passed_outcome_count: int, latest_result_met) -> bool:
    """The requirement the ranking models is supported by a recorded physical outcome that met the target. Any other
    requirement is supported by its latest recorded result, and only if that result met it."""
    if req.get("is_ranking_target"):
        return passed_outcome_count > 0
    return latest_result_met is True


def unsupported_critical(requirements: list, passed_outcome_count: int, latest_met: dict) -> list:
    """Names of active critical requirements without a supporting result."""
    return [r["property"] for r in requirements
            if r.get("criticality") == "critical" and not r.get("archived_at")
            and not requirement_supported(r, passed_outcome_count, latest_met.get(r["id"]))]


def approval_problem(missing: list):
    if not missing:
        return None
    return "Critical requirements without a recorded result that met them: " + ", ".join(missing) + "."


def completion_problem(has_critical: bool, missing: list, stage: str):
    """Closing a program that has critical requirements needs a recorded decision. A rejection records that the program
    will not proceed. Otherwise every critical requirement must be supported by a result that met it, and the program
    must have been approved."""
    if not has_critical or stage == "rejected":
        return None
    if missing:
        return ("This program has critical requirements without a recorded result that met them: " + ", ".join(missing)
                + ". Record the results, or reject the program, before closing it.")
    if stage != "approved":
        return "Record an approval or a rejection before closing a program that has critical requirements."
    return None


def is_overdue(due, completed_at, today: str) -> bool:
    return bool(due) and not completed_at and due < today
