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

def check_candidate_features_complete(feature_columns: list, candidate_features: dict,
                                       candidate_name: str = None) -> None:
    """Priority 6 (A2/A3): a candidate must supply a value for every feature
    the change case's qualification_spec requires -- otherwise ranking would
    later dereference a missing dict key (an accidental KeyError, not a
    deliberate business error). Used both at candidate creation (A2) and
    defensively again immediately before ranking (A3), so a malformed
    candidate can never reach the engine however it was created.

    If the case does not yet have a complete feature specification
    (feature_columns is empty/None), this is a no-op -- preserves the
    existing behavior of not inventing a requirement the case hasn't
    defined yet (mirrors how dataset upload only checks spec completeness
    once, not before)."""
    if not feature_columns:
        return
    missing = [col for col in feature_columns if col not in candidate_features]
    if missing:
        who = f"Candidate '{candidate_name}'" if candidate_name else "This candidate"
        raise ValidationError(
            f"{who} is missing required feature(s): {', '.join(missing)}. "
            f"This change case's qualification spec requires: {', '.join(feature_columns)}.",
            field="features",
        )


def compute_data_quality_metrics(rows: list, feature_columns: list) -> tuple:
    """Pure function shared by CSV ingestion and by the ranking/report
    gates, so the definition of 'duplicate' and 'constant' can't drift
    between them. `rows` is a list of {"features": {col: float},
    "target_value": float}. Returns (duplicate_count, constant_columns).
    A duplicate is an exact (features, target) repeat of an earlier row."""
    seen = set()
    duplicate_count = 0
    for r in rows:
        key = (tuple(sorted(r["features"].items())), r["target_value"])
        if key in seen:
            duplicate_count += 1
        seen.add(key)
    constant_columns = [
        col for col in feature_columns
        if len({r["features"][col] for r in rows}) <= 1
    ]
    return duplicate_count, constant_columns


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


def check_data_quality_allows_ranking(data_quality_status: str) -> None:
    """A ranking is the paid diagnostic's core output, so it is refused
    on data that cannot support one: 'invalid' (e.g. a zero-variance
    feature) or 'insufficient' (too few DISTINCT rows once exact
    duplicates are removed -- duplicates must not inflate evidence)."""
    if data_quality_status == "invalid":
        raise ValidationError(
            "This dataset has a data-quality issue (e.g. a feature column with "
            "no variance) that must be resolved before a reliable ranking can be "
            "produced.",
            field="data_quality_status",
        )
    if data_quality_status == "insufficient":
        raise ValidationError(
            f"After removing exact duplicate rows, this dataset has fewer than "
            f"{MIN_HISTORICAL_ROWS_FOR_PREDICTION} distinct historical records -- "
            "not enough evidence for a reliable ranking (data-quality check).",
            field="data_quality_status",
        )


# ---------------------------------------------------------------------------
# Priority 5 -- domain / extrapolation coverage
# ---------------------------------------------------------------------------

DOMAIN_EDGE_MARGIN_PCT = 0.10  # HEURISTIC, NOT SCIENTIFICALLY VALIDATED -- an explicit, adjustable business threshold

DOMAIN_WITHIN = "within_historical_domain"
DOMAIN_NEAR_EDGE = "near_edge_of_domain"
DOMAIN_OUTSIDE = "outside_historical_domain"
_DOMAIN_SEVERITY = {DOMAIN_WITHIN: 0, DOMAIN_NEAR_EDGE: 1, DOMAIN_OUTSIDE: 2}


def domain_coverage_note(edge_margin_pct: float = DOMAIN_EDGE_MARGIN_PCT) -> str:
    """Plain-language description of the heuristic, for the API and the
    report. Deliberately states that the margin is NOT scientifically
    derived -- this text must never imply otherwise."""
    pct = f"{edge_margin_pct:.0%}"
    return (
        "Historical-range coverage is a heuristic check, not a scientifically validated "
        f"measure. A candidate is 'near edge' when a feature lies within {pct} of that "
        "feature's observed range beyond the historical minimum or maximum, and 'outside' "
        f"beyond that. The {pct} margin is an adjustable business threshold, not derived "
        "from data or theory. Predictions for candidates near or outside the historical "
        "range are extrapolations and warrant particular caution."
    )


def classify_domain_coverage(candidate_value: float, historical_min: float, historical_max: float,
                              edge_margin_pct: float = DOMAIN_EDGE_MARGIN_PCT) -> str:
    """Classifies ONE feature value against the observed historical range.

    - within the observed [min, max]                      -> within_historical_domain
    - beyond a bound, but by no more than edge_margin_pct
      of the observed range (max - min), inclusive        -> near_edge_of_domain
    - further than that                                   -> outside_historical_domain

    edge_margin_pct is a HEURISTIC, adjustable business threshold -- not a
    scientifically derived one (see DOMAIN_EDGE_MARGIN_PCT). For a
    zero-width range (a constant column) the margin is zero, so any value
    other than that constant is outside."""
    if edge_margin_pct < 0:
        raise ValueError("edge_margin_pct must be non-negative.")
    if historical_min > historical_max:
        raise ValueError("historical_min must not exceed historical_max.")
    if historical_min <= candidate_value <= historical_max:
        return DOMAIN_WITHIN
    margin = (historical_max - historical_min) * edge_margin_pct
    if historical_min - margin <= candidate_value <= historical_max + margin:
        return DOMAIN_NEAR_EDGE
    return DOMAIN_OUTSIDE


def compute_historical_ranges(rows: list, feature_columns: list) -> dict:
    """{feature: (min, max)} over the given historical rows (each
    {"features": {col: float}, ...}). Pure; raises ValueError if there are
    no rows, since a range can't be defined without data."""
    if not rows:
        raise ValueError("Cannot compute historical ranges without historical rows.")
    return {
        col: (min(r["features"][col] for r in rows), max(r["features"][col] for r in rows))
        for col in feature_columns
    }


def classify_candidate_domain_coverage(candidate_features: dict, historical_ranges: dict,
                                        edge_margin_pct: float = DOMAIN_EDGE_MARGIN_PCT) -> dict:
    """Per-feature classification plus an overall status that is the WORST
    across features (a candidate is only 'within' if every feature is)."""
    features = {}
    overall = DOMAIN_WITHIN
    for col, (lo, hi) in historical_ranges.items():
        value = candidate_features[col]
        status = classify_domain_coverage(value, lo, hi, edge_margin_pct)
        features[col] = {"status": status, "value": value, "historical_min": lo, "historical_max": hi}
        if _DOMAIN_SEVERITY[status] > _DOMAIN_SEVERITY[overall]:
            overall = status
    return {
        "status": overall,
        "edge_margin_pct": edge_margin_pct,
        "features": features,
        "note": domain_coverage_note(edge_margin_pct),
    }