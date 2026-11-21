"""Workflow stages of a program (a change case) and the rules for moving between them. Pure: no database access.

Stage is a record of where the work stands. It is not a qualification result, and moving to 'approved' does not
qualify a material: that needs the requirement level evidence added in later levels."""
from backend.app.schemas.errors import ValidationError

INTAKE, REQUIREMENTS, EVIDENCE_COLLECTION, EVIDENCE_REVIEW = "intake", "requirements", "evidence_collection", "evidence_review"
CANDIDATE_ASSESSMENT, EXPERIMENT_PLANNING = "candidate_assessment", "experiment_planning"
PHYSICAL_VALIDATION, ENGINEERING_REVIEW = "physical_validation", "engineering_review"
APPROVED, REJECTED, CLOSED = "approved", "rejected", "closed"

# The working stages in order. Forward moves go one step at a time.
WORKFLOW = (INTAKE, REQUIREMENTS, EVIDENCE_COLLECTION, EVIDENCE_REVIEW, CANDIDATE_ASSESSMENT,
            EXPERIMENT_PLANNING, PHYSICAL_VALIDATION, ENGINEERING_REVIEW)
DECISIONS = (APPROVED, REJECTED)
ALL_STAGES = WORKFLOW + DECISIONS + (CLOSED,)

LABELS = {
    INTAKE: "Intake", REQUIREMENTS: "Requirements", EVIDENCE_COLLECTION: "Evidence collection",
    EVIDENCE_REVIEW: "Evidence review", CANDIDATE_ASSESSMENT: "Candidate assessment",
    EXPERIMENT_PLANNING: "Experiment planning", PHYSICAL_VALIDATION: "Physical validation",
    ENGINEERING_REVIEW: "Engineering review", APPROVED: "Approved", REJECTED: "Rejected", CLOSED: "Closed",
}

# Stages that need the program to have been started (status 'active'); a draft can only be set up.
NEEDS_ACTIVE = set(WORKFLOW[WORKFLOW.index(CANDIDATE_ASSESSMENT):]) | set(DECISIONS)
DECISION_ROLES = ("owner", "admin")
MAX_REASON_CHARS = 500


def label(stage: str) -> str:
    return LABELS.get(stage, stage)


def allowed_targets(current: str) -> list:
    """Stages a program at `current` may move to, before any evidence check."""
    if current == CLOSED or current not in ALL_STAGES:
        return []
    if current in DECISIONS:
        return [ENGINEERING_REVIEW]  # reopen the decision
    i = WORKFLOW.index(current)
    targets = list(WORKFLOW[:i])  # backwards: any earlier step, with a reason
    if i + 1 < len(WORKFLOW):
        targets.append(WORKFLOW[i + 1])
    if current == ENGINEERING_REVIEW:
        targets.extend(DECISIONS)
    return targets


def is_backward(current: str, target: str) -> bool:
    if current in DECISIONS:
        return True
    return current in WORKFLOW and target in WORKFLOW and WORKFLOW.index(target) < WORKFLOW.index(current)


def _evidence_problem(target: str, facts: dict):
    """The record based requirement for entering `target`, or None. `facts` holds counts of real records."""
    if target in (EVIDENCE_REVIEW, CANDIDATE_ASSESSMENT) and facts["dataset_count"] < 1:
        return "Upload evidence before this step."
    if target == EXPERIMENT_PLANNING and facts["prediction_count"] < 1:
        return "Rank candidates before planning experiments."
    if target == PHYSICAL_VALIDATION and facts["prediction_count"] < 1:
        return "Rank candidates before physical validation."
    if target == ENGINEERING_REVIEW and facts["outcome_count"] < 1:
        return "Record at least one physical result before engineering review."
    if target == APPROVED and facts["passed_outcome_count"] < 1:
        return "Approval needs at least one recorded physical result that met the target."
    return None


def check_transition(current: str, target: str, status: str, archived: bool, role: str, reason: str, facts: dict) -> str:
    """Raises ValidationError when the move is not allowed; returns the cleaned reason otherwise."""
    if target not in ALL_STAGES:
        raise ValidationError("Choose a valid stage.", field="stage")
    if archived:
        raise ValidationError("This program is archived. Restore it to change its stage.", field="stage")
    if status == "completed" or current == CLOSED:
        raise ValidationError("This program is closed and its stage cannot change.", field="stage")
    if target == CLOSED:
        raise ValidationError("Close the program from its status instead.", field="stage")
    if target == current:
        raise ValidationError("The program is already at this stage.", field="stage")
    if target not in allowed_targets(current):
        raise ValidationError(f"A program at {label(current).lower()} cannot move to {label(target).lower()}.", field="stage")
    if target in NEEDS_ACTIVE and status != "active":
        raise ValidationError("Start the program before moving past evidence collection.", field="stage")
    if target in DECISIONS and role not in DECISION_ROLES:
        raise ValidationError("Only an owner or admin can record an approval or rejection.", field="stage")
    problem = _evidence_problem(target, facts)
    if problem:
        raise ValidationError(problem, field="stage")
    cleaned = (reason or "").strip()
    if len(cleaned) > MAX_REASON_CHARS:
        raise ValidationError(f"Keep the reason under {MAX_REASON_CHARS} characters.", field="reason")
    if (is_backward(current, target) or target in DECISIONS) and len(cleaned) < 3:
        raise ValidationError("Add a reason for this change.", field="reason")
    return cleaned
