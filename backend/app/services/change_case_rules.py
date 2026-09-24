"""
Pure business rules for the change-case qualification domain, with NO
fastapi/sqlalchemy/pydantic import -- same pattern as project_rules.py
and team_rules.py, and for the same reason: this logic is testable in a
sandbox where those packages aren't installed, and it isolates the rules
that actually matter (safety, data integrity, entitlement) from web/DB
plumbing so they can't be silently bypassed by a route that forgets to
call them.
"""
from datetime import datetime, timezone

from backend.app.schemas.errors import ValidationError

MIN_HISTORICAL_ROWS_FOR_PREDICTION = 8

VALID_STATUS_TRANSITIONS = {
    "draft": {"active"},
    "active": {"completed"},
    "completed": set(),
}


def check_sufficient_data_for_prediction(historical_row_count: int) -> None:
    """Raises ValidationError if there isn't enough historical qualification
    data to produce a meaningful candidate-ranking prediction. Mirrors the
    engine's own `compute_model_metrics` guard (`test_rejects_too_little_data`
    in engine/tests/test_optimization.py) -- enforced here too so a route
    can reject early with a clear message instead of the engine failing
    deep inside a GP fit with a confusing error."""
    if historical_row_count < MIN_HISTORICAL_ROWS_FOR_PREDICTION:
        raise ValidationError(
            f"Need at least {MIN_HISTORICAL_ROWS_FOR_PREDICTION} historical "
            "qualification records to generate a reliable prediction -- with "
            "fewer than that, any ranking would be little more than a guess, "
            "and we'd rather say so than present false confidence.",
            field="historical_row_count",
        )


def check_status_transition_allowed(current_status: str, new_status: str) -> None:
    """Raises ValidationError on an invalid change-case status transition.
    A completed change case is a closed record (its qualification_outcomes
    are the historical asset) and can never be reopened; draft can only
    move forward to active, never skip to completed without going through
    it, keeping the lifecycle simple and auditable."""
    allowed = VALID_STATUS_TRANSITIONS.get(current_status)
    if allowed is None:
        raise ValidationError(f"Unknown change case status: {current_status!r}", field="status")
    if new_status not in allowed:
        raise ValidationError(
            f"Can't move a change case from '{current_status}' to '{new_status}'.",
            field="status",
        )


def check_outcome_not_already_recorded(existing_outcome_count: int) -> None:
    """Raises ValidationError if an outcome already exists for this
    candidate. qualification_outcomes is append-only by design (see
    migrations/versions/2a47323d3b0c_*.py) -- correcting a mistaken entry
    means recording a NEW outcome row referencing the correction, never
    updating or deleting the original, so the historical record (which is
    the company's core proprietary asset) is never silently rewritten."""
    if existing_outcome_count > 0:
        raise ValidationError(
            "An outcome is already recorded for this candidate. Outcomes "
            "are permanent once recorded -- record a new, corrected outcome "
            "referencing this one rather than overwriting it.",
            field="candidate_id",
        )


def check_entitled_for_diagnostic(plan: str, subscription_status: str) -> None:
    """Raises ValidationError if this organization's current plan/billing
    status doesn't entitle them to run a new change-case diagnostic.
    Deliberately simple for Phase 1 (no seat counts, no usage metering) --
    entitlement logic can grow here without ever touching billing_service.py
    or routes directly, per PROJECT_ARCHITECTURE.md's billing-boundary rule."""
    if subscription_status not in ("active",):
        raise ValidationError(
            "This organization's subscription isn't active -- billing "
            "must be current to run a new diagnostic.",
            field="subscription_status",
        )
    if plan == "trial":
        raise ValidationError(
            "Trial organizations can't run a full diagnostic yet -- "
            "upgrade to a paid plan to continue.",
            field="plan",
        )


def is_change_case_expired(expires_at: str, now: datetime = None) -> bool:
    """A change case tied to a regulatory deadline can carry an expiry
    (e.g. the compliance deadline it exists to address has passed).
    Mirrors the timezone-safe comparison already proven correct in
    team_rules.py::check_invitation_usable -- accepts a naive-or-aware
    ISO string and always compares in UTC."""
    now = now or datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(expires_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return now > parsed


MAX_CANDIDATES_PER_CHANGE_CASE = 5


def check_can_add_candidate(change_case_status: str, existing_candidate_count: int) -> None:
    """Raises ValidationError if a candidate can't be added right now.
    Candidates can only be added while a change case is still open for
    input ('draft' or 'active') -- never after it's 'completed', which is
    a closed historical record. Also enforces the deliberate Phase 2 scope
    limit of customer-supplied candidates: 3-5 NAMED candidates, never an
    open-ended or generated pool -- this product does not do candidate
    discovery/recommendation, by design."""
    if change_case_status not in ("draft", "active"):
        raise ValidationError(
            f"Can't add a candidate to a change case with status '{change_case_status}'.",
            field="status",
        )
    if existing_candidate_count >= MAX_CANDIDATES_PER_CHANGE_CASE:
        raise ValidationError(
            f"This change case already has {MAX_CANDIDATES_PER_CHANGE_CASE} candidates, "
            "the maximum for this diagnostic. Remove one before adding another, or start "
            "a new change case for additional candidates.",
            field="candidates",
        )


def check_can_trigger_ranking(historical_row_count: int, candidate_count: int) -> None:
    """Raises ValidationError if a ranking run can't be triggered yet.
    Composes check_sufficient_data_for_prediction (historical data side)
    with a minimum candidate-count check (at least one NAMED candidate
    must exist -- ranking zero candidates is meaningless)."""
    check_sufficient_data_for_prediction(historical_row_count)
    if candidate_count < 1:
        raise ValidationError(
            "Add at least one candidate substitute before requesting a ranking.",
            field="candidates",
        )

def classify_data_quality(historical_row_count: int, duplicate_count: int, constant_columns: list) -> str:
    """Returns 'invalid', 'insufficient', or 'valid'. Never silently
    proceeds past a real data problem -- constant columns make a
    dataset unusable for prediction regardless of row count; too few
    genuinely distinct rows (after removing duplicates) means there
    isn't enough evidence to trust a prediction."""
    if constant_columns:
        return "invalid"
    if historical_row_count - duplicate_count < MIN_HISTORICAL_ROWS_FOR_PREDICTION:
        return "insufficient"
    return "valid"


def check_can_generate_report(data_quality_status: str) -> None:
    if data_quality_status == "invalid":
        raise ValidationError(
            "This dataset has a data-quality issue (e.g. a feature column with "
            "no variance) that must be resolved before a reliable report can be "
            "generated.",
            field="data_quality_status",
        )