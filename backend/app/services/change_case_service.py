"""
Orchestrates the forced-substitution qualification diagnostic workflow:

change case -> qualification dataset -> named candidates -> ranking

(via engine.facade.rank_candidates) -> recommended experiments ->

recorded outcomes -> report.



Every write path here calls into change_case_rules.py first -- this

service never bypasses a rule check to save a round trip. This is the

second (and, by design, should stay a small number of) service that

imports from `engine` -- see optimization_service.py's own docstring for

the same discipline in the original domain.



Entitlement is checked at the point value is actually delivered --

triggering a ranking run (rank_change_case below) -- not at change-case

creation. A trial-plan organization can create a case and upload data,

but the paid diagnostic itself (the ranking + recommended experiments)

requires an active, non-trial subscription. This was an explicit open

design decision from the approved Phase 2 plan, resolved here because

the commercial entry product IS the diagnostic (ranking + report), not

case setup.

"""

import json


import numpy as np


from engine.facade import rank_candidates as engine_rank_candidates


from backend.app.repositories import (
    change_case_repo,
    qualification_dataset_repo,
    candidate_repo,
    subscriptions_repo,
)

from backend.app.services import change_case_rules

from backend.app.schemas.errors import ValidationError





def create_change_case(organization_id: int, created_by_user_id: int, name: str,
                       trigger_type: str, restricted_substance: str,
                       qualification_spec: dict) -> dict:

    change_case_id = change_case_repo.create_change_case(
        organization_id, created_by_user_id, name, trigger_type,
        restricted_substance, json.dumps(qualification_spec),
    )

    return change_case_repo.get_change_case(organization_id, change_case_id)





def get_change_case_or_404(organization_id: int, change_case_id: int) -> dict:

    case = change_case_repo.get_change_case(organization_id, change_case_id)

    if case is None:

        raise LookupError("Change case not found.")

    return case





def update_status(organization_id: int, change_case_id: int, new_status: str) -> dict:

    case = get_change_case_or_404(organization_id, change_case_id)

    change_case_rules.check_status_transition_allowed(case["status"], new_status)

    change_case_repo.update_status(organization_id, change_case_id, new_status)

    return change_case_repo.get_change_case(organization_id, change_case_id)


def add_candidate(organization_id: int, change_case_id: int, name: str, features: dict) -> dict:
    case = get_change_case_or_404(organization_id, change_case_id)
    existing_count = candidate_repo.count_candidates_for_change_case(organization_id, change_case_id)
    change_case_rules.check_can_add_candidate(case["status"], existing_count)
    # Priority 6 (A2): reject an incomplete candidate up front, once the case
    # has a qualification_spec with feature_columns defined -- otherwise a
    # candidate missing a required feature would silently be stored and only
    # fail later, opaquely, when ranking tries to read the missing feature.
    spec = json.loads(case["qualification_spec_json"])
    change_case_rules.check_candidate_features_complete(spec.get("feature_columns"), features, name)
    candidate_id = candidate_repo.create_candidate(organization_id, change_case_id, name, features)
    return candidate_repo.get_candidate(organization_id, candidate_id)


def _historical_ranges(spec: dict, experiments: list):
    """{feature: (min, max)} from one dataset version's stored rows, or
    None if that dataset has no rows."""
    if not experiments:
        return None
    rows = [{"features": json.loads(e["features_json"])} for e in experiments]
    return change_case_rules.compute_historical_ranges(rows, spec["feature_columns"])


def list_candidates_with_predictions(organization_id: int, change_case_id: int) -> list:
    candidates = candidate_repo.list_candidates_for_change_case(organization_id, change_case_id)
    spec = None
    ranges_by_dataset = {}
    for c in candidates:
        c["properties"] = json.loads(c["properties_json"])
        c["latest_prediction"] = candidate_repo.get_latest_prediction_for_candidate(organization_id, c["id"])
        c["domain_coverage"] = None
        pred = c["latest_prediction"]
        if pred is None:
            continue
        # Domain coverage is judged against the dataset THIS prediction was
        # generated from (dataset_version_id) -- never the latest upload --
        # so a later, wider dataset can't quietly turn an extrapolation into
        # an in-range candidate.
        if spec is None:
            case = get_change_case_or_404(organization_id, change_case_id)
            spec = json.loads(case["qualification_spec_json"])
        dataset_id = pred["dataset_version_id"]
        if dataset_id not in ranges_by_dataset:
            experiments = qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset_id)
            ranges_by_dataset[dataset_id] = _historical_ranges(spec, experiments)
        ranges = ranges_by_dataset[dataset_id]
        if ranges is not None:
            c["domain_coverage"] = change_case_rules.classify_candidate_domain_coverage(c["properties"], ranges)
    return candidates


def _check_entitled(organization_id: int) -> None:
    subscription = subscriptions_repo.get_subscription(organization_id)
    if subscription is None:
        raise ValidationError("No subscription found for this organization.", field="subscription")
    change_case_rules.check_entitled_for_diagnostic(subscription["plan"], subscription["status"])


def _assess_dataset_quality(spec: dict, dataset: dict, experiments: list) -> dict:
    """Recomputes data quality from the STORED historical rows of one
    specific dataset version. Datasets are immutable once uploaded, so the
    result is deterministic for a given dataset_id -- no upload-time value
    needs to be persisted, and the gates cannot be bypassed by skipping the
    upload response."""
    rows = [
        {"features": json.loads(e["features_json"]), "target_value": e["target_value"]}
        for e in experiments
    ]
    duplicate_count, constant_columns = change_case_rules.compute_data_quality_metrics(
        rows, spec["feature_columns"],
    )
    return {
        "status": change_case_rules.classify_data_quality(len(rows), duplicate_count, constant_columns),
        "dataset_id": dataset["id"],
        "row_count": len(rows),
        "distinct_rows": len(rows) - duplicate_count,
        "duplicate_rows": duplicate_count,
        "constant_columns": constant_columns,
    }


def rank_change_case(organization_id: int, change_case_id: int) -> list:
    """The paid diagnostic action: scores every named candidate against
    the change case's uploaded historical qualification data, writes a
    fresh prediction + a minimum recommended experiment for each, and
    returns the ranked result. Requires an active, non-trial
    subscription (see module docstring).

    Ownership is checked BEFORE entitlement, deliberately: a request for
    a change case that doesn't exist or belongs to another organization
    must always behave identically (404) regardless of the requesting
    organization's own plan status -- checking entitlement first would
    mean a trial-plan requester gets a different error (400, their own
    plan) than a paid one (404) when probing another org's change_case_id,
    which leaks a bit of information about the requester's own account
    state into an otherwise-uniform 404 response. No customer data is
    exposed either way, but the check order matters for consistency with
    change_case_repo.get_change_case's own documented 'never distinguish
    not-found from not-yours' rule."""
    case = get_change_case_or_404(organization_id, change_case_id)
    _check_entitled(organization_id)

    dataset = qualification_dataset_repo.get_latest_dataset_for_change_case(organization_id, change_case_id)
    if dataset is None:
        raise ValidationError(
            "Upload a qualification dataset before requesting a ranking.",
            field="dataset",
        )
    experiments = qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset["id"])
    candidates = candidate_repo.list_candidates_for_change_case(organization_id, change_case_id)

    change_case_rules.check_can_trigger_ranking(len(experiments), len(candidates))

    spec = json.loads(case["qualification_spec_json"])
    # Quality is judged on the SAME dataset the new predictions will be
    # stamped with (dataset["id"] below) -- the latest upload, by design,
    # because ranking is a new analysis on the newest data.
    change_case_rules.check_data_quality_allows_ranking(
        _assess_dataset_quality(spec, dataset, experiments)["status"]
    )
    feature_columns = spec["feature_columns"]
    direction = spec.get("direction", "maximize")
    target_value = spec["target_value"]

    # Priority 6 (A3): defensive second layer, independent of add_candidate's
    # own check (A2) -- a candidate created before the case had a complete
    # spec, or reaching this point by any other path, must still never be
    # able to crash the engine with an accidental KeyError. Validate ALL
    # candidates up front, before any engine call, so a bad one is reported
    # cleanly rather than failing partway through ranking.
    for c in candidates:
        change_case_rules.check_candidate_features_complete(
            feature_columns, json.loads(c["properties_json"]), c["candidate_name"],
        )

    historical_ranges = _historical_ranges(spec, experiments)  # same dataset the predictions are stamped with

    X = np.array([[json.loads(e["features_json"])[col] for col in feature_columns] for e in experiments])
    y = np.array([e["target_value"] for e in experiments])

    from engine.facade import compute_model_metrics, compute_uncertainty_calibration
    model_quality = compute_model_metrics(X, y)
    uncertainty_calibration = (
        compute_uncertainty_calibration(X, y) if "error" not in model_quality else None
    )

    engine_candidates = [
        {"name": c["candidate_name"], "features": json.loads(c["properties_json"])}
        for c in candidates
    ]

    ranked = engine_rank_candidates(X, y, direction, feature_columns, target_value, engine_candidates)

    results = []
    name_to_id = {c["candidate_name"]: c["id"] for c in candidates}
    for r in ranked:
        candidate_id = name_to_id[r["name"]]
        candidate_repo.create_prediction(
            organization_id, candidate_id, dataset["id"], r["model_version"],
            r["predicted_probability"], r["uncertainty_std"],
        )
        domain_coverage = change_case_rules.classify_candidate_domain_coverage(r["features"], historical_ranges)
        experiment_description = _minimum_experiment_description(r, target_value, direction, domain_coverage)
        candidate_repo.create_recommended_experiment(organization_id, candidate_id, experiment_description, priority=1)
        results.append({
            "candidate_id": candidate_id,
            "candidate_name": r["name"],
            "predicted_probability": r["predicted_probability"],
            "uncertainty_std": r["uncertainty_std"],
            "recommended_experiment": experiment_description,
            "domain_coverage": domain_coverage,
        })

    return results


def _minimum_experiment_description(ranked_candidate: dict, target_value: float, direction: str,
                                     domain_coverage: dict = None) -> str:
    """A simple, honest, rule-based minimum-validation-experiment
    description -- deliberately NOT sophisticated active-learning
    experiment design in this phase (that's flagged as a known,
    accepted limitation, not built here). States plainly that this is
    the recommended NEXT physical validation step, not a claim that
    physical qualification itself is complete or fast."""
    prob = ranked_candidate["predicted_probability"]
    if prob >= 0.8:
        confidence_note = "high predicted confidence"
    elif prob >= 0.4:
        confidence_note = "moderate predicted confidence -- validation is important before relying on this candidate"
    else:
        confidence_note = "low predicted confidence -- likely not worth physical validation unless no better candidate exists"
    description = (
        f"Run a physical qualification test against the target spec for "
        f"'{ranked_candidate['name']}' ({confidence_note}, predicted "
        f"probability {prob:.0%}). This analysis identifies which "
        f"candidate to test first -- it does not replace running and "
        f"recording the actual physical result."
    )
    if domain_coverage and domain_coverage["status"] != change_case_rules.DOMAIN_WITHIN:
        flagged = ", ".join(
            col for col, f in domain_coverage["features"].items()
            if f["status"] != change_case_rules.DOMAIN_WITHIN
        )
        where = ("outside" if domain_coverage["status"] == change_case_rules.DOMAIN_OUTSIDE
                 else "near the edge of")
        description += (
            f" Domain caution: this candidate lies {where} the range of the historical "
            f"data for {flagged}, so the prediction is an extrapolation -- physical "
            f"validation is essential before relying on it."
        )
    return description


def record_outcome(organization_id: int, candidate_id: int, recommended_experiment_id,
                   actual_result: str, passed_spec: bool, recorded_by_user_id: int) -> dict:
    existing_count = candidate_repo.count_outcomes_for_candidate(organization_id, candidate_id)
    change_case_rules.check_outcome_not_already_recorded(existing_count)
    outcome_id = candidate_repo.create_outcome(
        organization_id, candidate_id, recommended_experiment_id, actual_result, passed_spec, recorded_by_user_id
    )
    return {"id": outcome_id, "candidate_id": candidate_id, "actual_result": actual_result, "passed_spec": passed_spec}


def generate_report(organization_id: int, change_case_id: int, organization_name: str) -> str:
    """Builds the qualification diagnostic .docx from the change case's
    latest candidates/predictions -- the commercial deliverable. Uses
    whatever ranking results currently exist (does not re-run ranking);
    call rank_change_case first if a fresh ranking is wanted."""
    from backend.app.services import report_builder

    case = get_change_case_or_404(organization_id, change_case_id)
    candidates = list_candidates_with_predictions(organization_id, change_case_id)

    # Dataset provenance: a report is evaluated against the dataset its
    # predictions were generated from (predictions.dataset_version_id) --
    # NEVER silently against a newer upload. Only when no predictions exist
    # yet (so there is no provenance to preserve) does it fall back to the
    # latest dataset, which is then simply the data basis of an empty report.
    prediction_dataset_ids = {
        c["latest_prediction"]["dataset_version_id"]
        for c in candidates if c.get("latest_prediction")
    }
    if len(prediction_dataset_ids) > 1:
        raise ValidationError(
            "Candidate predictions in this change case were generated from different "
            "dataset versions. Re-run the ranking so every candidate is scored against "
            "the same dataset before generating a report.",
            field="dataset",
        )
    if prediction_dataset_ids:
        dataset = qualification_dataset_repo.get_dataset_for_change_case(
            organization_id, change_case_id, next(iter(prediction_dataset_ids))
        )
    else:
        dataset = qualification_dataset_repo.get_latest_dataset_for_change_case(organization_id, change_case_id)

    n_historical_rows = dataset["row_count"] if dataset else 0
    data_quality = None
    if dataset:
        experiments = qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset["id"])
        spec = json.loads(case["qualification_spec_json"])
        data_quality = _assess_dataset_quality(spec, dataset, experiments)
        change_case_rules.check_can_generate_report(data_quality["status"])

    ranked_results = []
    for c in candidates:
        pred = c.get("latest_prediction")
        if pred is None:
            continue
        experiments = candidate_repo.list_recommended_experiments_for_candidate(organization_id, c["id"])
        description = experiments[0]["description"] if experiments else "No experiment recommended yet."
        ranked_results.append({
            "candidate_name": c["candidate_name"],
            "predicted_probability": pred["predicted_probability"],
            "uncertainty_std": pred["uncertainty_std"],
            "recommended_experiment": description,
            "domain_coverage": c.get("domain_coverage"),
        })
    ranked_results.sort(key=lambda r: r["predicted_probability"], reverse=True)

    return report_builder.build_qualification_report(
        case, ranked_results, n_historical_rows, organization_name, data_quality=data_quality,
    )