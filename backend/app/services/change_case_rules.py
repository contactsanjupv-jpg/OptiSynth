"""
Pure business rules for the change-case qualification domain, with NO
fastapi/sqlalchemy/pydantic import -- same pattern as project_rules.py
and team_rules.py, and for the same reason: this logic is testable in a
sandbox where those packages aren't installed, and it isolates the rules
that actually matter (safety, data integrity, entitlement) from web/DB
plumbing so they can't be silently bypassed by a route that forgets to
call them.
"""

import math

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
        raise ValidationError(
            f"Unknown change case status: {current_status!r}", field="status"
        )

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


def check_candidate_features_finite(candidate_features: dict, candidate_name: str = None) -> None:
    """Priority 7A (B2): NaN and +/-Infinity are technically valid Python
    floats -- float('nan') and float('inf') succeed, and Python's json
    decoder accepts the literal tokens NaN/Infinity by default -- so
    neither float() coercion nor Pydantic's `dict[str, float]` type check
    rejects them. Without this check they would silently reach the GP,
    where sklearn raises a raw, unhandled ValueError ('Input X contains
    NaN') instead of a clean application error. Mirrors
    check_candidate_features_complete's per-candidate clarity."""
    non_finite = [col for col, v in candidate_features.items() if not math.isfinite(v)]

    if non_finite:
        who = f"Candidate '{candidate_name}'" if candidate_name else "This candidate"
        raise ValidationError(
            f"{who} has a non-finite value (NaN or Infinity) for feature(s): "
            f"{', '.join(non_finite)}. Feature values must be finite numbers.",
            field="features",
        )


def check_rows_are_finite(rows: list) -> None:
    """Priority 7A (B2), defense-in-depth: a second, historical-data-side
    check immediately before ranking, independent of ingestion's own
    finite-value rejection -- protects against any row that reached
    storage before this check existed, or by any other path. `rows` is a
    list of {"features": {col: float}, "target_value": float}."""
    for r in rows:
        non_finite = [col for col, v in r["features"].items() if not math.isfinite(v)]

        if non_finite or not math.isfinite(r["target_value"]):
            raise ValidationError(
                "This dataset contains a non-finite value (NaN or Infinity) in a "
                "historical row and cannot be used for ranking. Re-upload a "
                "corrected dataset.",
                field="dataset",
            )


def check_candidate_features_complete(
    feature_columns: list,
    candidate_features: dict,
    candidate_name: str = None,
) -> None:
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


def classify_data_quality(
    historical_row_count: int,
    duplicate_count: int,
    constant_columns: list,
) -> str:
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

_DOMAIN_SEVERITY = {
    DOMAIN_WITHIN: 0,
    DOMAIN_NEAR_EDGE: 1,
    DOMAIN_OUTSIDE: 2,
}


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


def classify_domain_coverage(
    candidate_value: float,
    historical_min: float,
    historical_max: float,
    edge_margin_pct: float = DOMAIN_EDGE_MARGIN_PCT,
) -> str:
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
        col: (
            min(r["features"][col] for r in rows),
            max(r["features"][col] for r in rows),
        )
        for col in feature_columns
    }


def classify_candidate_domain_coverage(
    candidate_features: dict,
    historical_ranges: dict,
    edge_margin_pct: float = DOMAIN_EDGE_MARGIN_PCT,
) -> dict:
    """Per-feature classification plus an overall status that is the WORST
    across features (a candidate is only 'within' if every feature is)."""
    features = {}
    overall = DOMAIN_WITHIN

    for col, (lo, hi) in historical_ranges.items():
        value = candidate_features[col]
        status = classify_domain_coverage(value, lo, hi, edge_margin_pct)

        features[col] = {
            "status": status,
            "value": value,
            "historical_min": lo,
            "historical_max": hi,
        }

        if _DOMAIN_SEVERITY[status] > _DOMAIN_SEVERITY[overall]:
            overall = status

    return {
        "status": overall,
        "edge_margin_pct": edge_margin_pct,
        "features": features,
        "note": domain_coverage_note(edge_margin_pct),
    }


# ---------------------------------------------------------------------------
# C1 -- decision support (categorical; derived ONLY from domain coverage)
#
# The GP's predicted_probability is a model output given the supplied
# historical data, not a validated qualification probability. A candidate
# far outside the historical range can still receive a numerically
# maximal probability, so the probability alone must never be read as
# approval. decision_support is a SEPARATE, purely categorical signal that
# consumes the EXISTING domain-coverage status. It deliberately does NOT
# look at predicted_probability, sigma, or any model-quality metric, adds
# no numeric score, and introduces no second domain heuristic. The raw
# probability, sigma and domain_coverage are never modified by it.
# ---------------------------------------------------------------------------

DECISION_EVIDENCE_SUPPORTED = "evidence_supported"
DECISION_CAUTION = "caution"
DECISION_REQUIRES_VALIDATION = "requires_validation"
DECISION_SUPPORT_BASIS = "historical_domain_coverage"

_DECISION_BY_DOMAIN_STATUS = {
    DOMAIN_WITHIN: DECISION_EVIDENCE_SUPPORTED,
    DOMAIN_NEAR_EDGE: DECISION_CAUTION,
    DOMAIN_OUTSIDE: DECISION_REQUIRES_VALIDATION,
}

DECISION_LABELS = {
    DECISION_EVIDENCE_SUPPORTED: "Evidence-supported",
    DECISION_CAUTION: "Caution",
    DECISION_REQUIRES_VALIDATION: "Requires validation",
}

DECISION_STATEMENTS = {
    DECISION_EVIDENCE_SUPPORTED: (
        "Candidate lies within the observed range of every feature in the historical "
        "data, so the model estimate interpolates observed evidence. Physical "
        "validation is still required before any candidate is considered qualified."
    ),
    DECISION_CAUTION: (
        "Candidate lies just beyond the observed range of at least one feature "
        "(within the heuristic edge margin). The estimate is a mild extrapolation; "
        "validate physically before relying on it."
    ),
    DECISION_REQUIRES_VALIDATION: (
        "Candidate lies outside the observed range of at least one feature. The "
        "estimate is an extrapolation and is not evidence-supported for "
        "qualification without physical validation."
    ),
}

# Used only when a prediction exists but its historical-range coverage is
# unavailable / unrecognised. Status is still requires_validation (fail-safe).
DECISION_STATEMENT_COVERAGE_UNAVAILABLE = (
    "Historical-range coverage could not be determined for this candidate, so the "
    "model estimate is not evidence-supported for qualification without physical "
    "validation."
)

MODEL_ESTIMATE_NOTE = (
    "Model-estimated probability is the model's output given the supplied "
    "historical data. It is not a validated qualification probability."
)


def derive_decision_support(domain_coverage, has_prediction: bool = True):
    """Categorical decision-support signal for one candidate.

    - no prediction                                  -> None
    - prediction + within_historical_domain          -> evidence_supported
    - prediction + near_edge_of_domain               -> caution
    - prediction + outside_historical_domain         -> requires_validation
    - prediction + coverage missing/unrecognised     -> requires_validation
      (fail-safe: never defaults to evidence_supported)

    'evidence_supported' means ONLY that every feature lies within its
    observed historical range (a per-feature check, not a joint
    applicability domain). It does not mean qualified, validated, or
    guaranteed to generalise. Pure: depends on nothing but the coverage
    status -- not on probability, sigma, or model quality."""
    if not has_prediction:
        return None

    domain_status = (
        domain_coverage.get("status")
        if isinstance(domain_coverage, dict)
        else None
    )

    status = _DECISION_BY_DOMAIN_STATUS.get(domain_status)

    if status is None:
        return {
            "status": DECISION_REQUIRES_VALIDATION,
            "basis": DECISION_SUPPORT_BASIS,
            "domain_status": None,
            "label": DECISION_LABELS[DECISION_REQUIRES_VALIDATION],
            "statement": DECISION_STATEMENT_COVERAGE_UNAVAILABLE,
        }

    return {
        "status": status,
        "basis": DECISION_SUPPORT_BASIS,
        "domain_status": domain_status,
        "label": DECISION_LABELS[status],
        "statement": DECISION_STATEMENTS[status],
    }


def deduplicate_rows(rows: list) -> list:
    """C2: remove exact duplicate rows (same features AND same target_value),
    keeping the first occurrence in order. Uses the SAME key as
    compute_data_quality_metrics, so what is counted as a duplicate and what is
    removed from the model fit can never disagree. Rows that share inputs but
    differ in target are NOT duplicates (they are replicate/conflict cases,
    handled at intake)."""
    seen = set()
    kept = []
    for r in rows:
        key = (tuple(sorted(r["features"].items())), r["target_value"])
        if key in seen:
            continue
        seen.add(key)
        kept.append(r)
    return kept


# ---------------------------------------------------------------------------
# Evidence-sufficiency gate: one explicit, explainable answer to "can this
# evidence support a responsible analysis?". Pure; no database access.
#
# Hard stops (status "fail") reuse limits that already exist
# (MIN_HISTORICAL_ROWS_FOR_PREDICTION, constant columns, candidate
# completeness) plus ONE mathematically grounded rule: with fewer distinct
# rows than (features + 1) the fit is underdetermined. Everything else is a
# visible warning, never a silent pass. A candidate lying outside the
# historical range is deliberately NOT a hard stop: forced substitutes often
# do, and the product's honest answer for them is "requires validation".
# ---------------------------------------------------------------------------
SUFFICIENCY_PASS = "pass"
SUFFICIENCY_FAIL = "fail"
SUFFICIENCY_WARN = "warn"


def _check(code, status, title, detail):
    return {"code": code, "status": status, "title": title, "detail": detail}


def assess_evidence_sufficiency(spec: dict, experiment_rows: list, candidates: list,
                                has_dataset: bool, dataset_review: dict = None) -> dict:
    """experiment_rows: [{"features": {...}, "target_value": float}] of the
    CURRENT dataset. candidates: [{"candidate_name", "properties"}].
    -> {"can_rank", "checks", "blocking", "warnings", "summary"}"""
    feature_columns = list(spec.get("feature_columns") or [])
    checks = []

    if not has_dataset:
        checks.append(_check("DATASET_PRESENT", SUFFICIENCY_FAIL, "Reviewed historical evidence",
                             "No historical evidence has been accepted yet. Upload a file and complete the evidence "
                             "review before ranking."))
        distinct = []
    else:
        checks.append(_check("DATASET_PRESENT", SUFFICIENCY_PASS, "Reviewed historical evidence",
                             "Historical evidence has been accepted through the intake review."))
        distinct = deduplicate_rows(experiment_rows)

        n = len(distinct)
        if n < MIN_HISTORICAL_ROWS_FOR_PREDICTION:
            checks.append(_check("MIN_DISTINCT_ROWS", SUFFICIENCY_FAIL, "Enough distinct historical records",
                                 f"{n} distinct historical record(s); at least {MIN_HISTORICAL_ROWS_FOR_PREDICTION} are "
                                 "required. Add evidence (more qualified trials / test results) before relying on a model."))
        else:
            checks.append(_check("MIN_DISTINCT_ROWS", SUFFICIENCY_PASS, "Enough distinct historical records",
                                 f"{n} distinct historical records (minimum {MIN_HISTORICAL_ROWS_FOR_PREDICTION})."))

        if n < len(feature_columns) + 1:
            checks.append(_check("ROWS_VS_FEATURES", SUFFICIENCY_FAIL, "More records than input features",
                                 f"{n} distinct record(s) cannot determine a model with {len(feature_columns)} input "
                                 f"feature(s); at least {len(feature_columns) + 1} are needed. Reduce the features or "
                                 "add evidence."))
        else:
            checks.append(_check("ROWS_VS_FEATURES", SUFFICIENCY_PASS, "More records than input features",
                                 f"{n} distinct records for {len(feature_columns)} input feature(s)."))

        _, constant = compute_data_quality_metrics(experiment_rows, feature_columns)
        if constant:
            checks.append(_check("CONSTANT_COLUMNS", SUFFICIENCY_FAIL, "Every input feature varies",
                                 "These input features have the same value in every historical record, so nothing "
                                 f"can be learned about them: {', '.join(constant)}."))
        else:
            checks.append(_check("CONSTANT_COLUMNS", SUFFICIENCY_PASS, "Every input feature varies",
                                 "Every input feature takes more than one value in the historical records."))

        targets = {r["target_value"] for r in experiment_rows}
        if experiment_rows and len(targets) == 1:
            checks.append(_check("TARGET_VARIATION", SUFFICIENCY_WARN, "Target value varies",
                                 "Every historical record has the same target value; the model has no variation "
                                 "to learn from and its estimates carry little information."))

        if dataset_review is None:
            checks.append(_check("REVIEW_RECORD", SUFFICIENCY_WARN, "Intake review on record",
                                 "This dataset was uploaded before intake review records were kept: its column "
                                 "mapping, units and conditions were not recorded."))
        else:
            spec_units = spec.get("units") or {}
            no_unit = [c for c in feature_columns + [spec.get("target_metric")] if c and c not in spec_units]
            if no_unit:
                checks.append(_check("UNITS_DECLARED", SUFFICIENCY_WARN, "Units declared for every modelled field",
                                     "No canonical unit is declared for: " + ", ".join(no_unit) +
                                     ". Values are used as supplied and their comparability cannot be verified."))
            else:
                checks.append(_check("UNITS_DECLARED", SUFFICIENCY_PASS, "Units declared for every modelled field",
                                     "A canonical unit is declared for every modelled field."))

    if len(candidates) < 1:
        checks.append(_check("CANDIDATES_PRESENT", SUFFICIENCY_FAIL, "At least one named candidate",
                             "Add at least one customer-named candidate substitute."))
    else:
        incomplete = []
        for c in candidates:
            props = c.get("properties") or {}
            missing = [f for f in feature_columns if f not in props]
            if missing:
                incomplete.append(f"{c['candidate_name']} (missing {', '.join(missing)})")
        if incomplete:
            checks.append(_check("CANDIDATE_INPUTS_COMPLETE", SUFFICIENCY_FAIL, "Candidate inputs complete",
                                 "Candidates missing required inputs: " + "; ".join(incomplete) + "."))
        else:
            checks.append(_check("CANDIDATES_PRESENT", SUFFICIENCY_PASS, "At least one named candidate",
                                 f"{len(candidates)} candidate(s) named."))
            if has_dataset and experiment_rows and feature_columns:
                ranges = compute_historical_ranges(experiment_rows, feature_columns)
                outside = []
                for c in candidates:
                    cov = classify_candidate_domain_coverage(c["properties"], ranges)
                    if cov and cov["status"] != DOMAIN_WITHIN:
                        outside.append(c["candidate_name"])
                if outside:
                    checks.append(_check("CANDIDATES_OUTSIDE_RANGE", SUFFICIENCY_WARN, "Candidates inside the evidence",
                                         "These candidates lie outside (or at the edge of) the historical range of at "
                                         "least one feature, so their model estimates are extrapolations and will be "
                                         "marked 'Requires validation' / 'Caution': " + ", ".join(outside) + "."))

    blocking = [c for c in checks if c["status"] == SUFFICIENCY_FAIL]
    return {
        "can_rank": not blocking,
        "checks": checks,
        "blocking": blocking,
        "warnings": [c for c in checks if c["status"] == SUFFICIENCY_WARN],
        "summary": {"distinct_rows": len(distinct), "features": len(feature_columns), "candidates": len(candidates)},
    }
