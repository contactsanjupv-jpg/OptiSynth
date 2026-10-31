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

from backend.app.services import change_case_rules, evidence_rules

from backend.app.services import candidate_lifecycle_rules as cand_rules
from backend.app.services import ranking_evidence_rules

from backend.app.schemas.errors import ValidationError





def create_change_case(organization_id: int, created_by_user_id: int, name: str,
                       trigger_type: str, restricted_substance: str,
                       qualification_spec: dict) -> dict:

    # B3: optional 'units' in the spec are the canonical units the dataset and
    # candidate inputs are expressed in. Absent = unchanged behaviour.
    spec_problem = evidence_rules.validate_spec_extensions(qualification_spec)
    if spec_problem:
        raise ValidationError(spec_problem, field="qualification_spec")
    # Units are mandatory for every modelled field and the target, and cannot be edited later: a diagnostic
    # created without them could never be ranked. Enforced here (not only in the form) so that direct API calls,
    # scripts and any other caller get the same refusal.
    change_case_rules.check_units_declared_at_creation(qualification_spec)
    change_case_id = change_case_repo.create_change_case(
        organization_id, created_by_user_id, name, trigger_type,
        restricted_substance, json.dumps(qualification_spec),
    )

    case = change_case_repo.get_change_case(organization_id, change_case_id)
    # Say plainly what an explicit-but-unrecognised label means (exact-match only, not dimensionally validated).
    case["unit_notes"] = evidence_rules.custom_unit_notes(qualification_spec)
    return case





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


def validate_candidate_inputs(spec: dict, name: str, features: dict, units: dict, existing_names: list) -> tuple:
    """Every check a candidate must pass before it is stored -- ONE function, used by manual "Add candidate" and
    by the CSV import alike, so an imported candidate is never accepted on weaker terms than a typed one.
    Raises ValidationError with the first problem. -> (properties_in_canonical_units, input_json_or_None).

    No product-level cap on candidates, but names must be unique within the diagnostic (trimmed,
    case-insensitive) so two candidates can never be confused in the ranking or the report. `existing_names`
    must include candidates removed from the analysis too: restoring one must never create a duplicate name."""
    change_case_rules.check_candidate_name_unique(name, existing_names)
    # Priority 6 (A2): reject an incomplete candidate up front, once the case has a qualification_spec with
    # feature_columns defined -- otherwise a candidate missing a required feature would silently be stored and
    # only fail later, opaquely, when ranking tries to read the missing feature.
    change_case_rules.check_candidate_features_complete(spec.get("feature_columns"), features, name)
    # Level 1D: a value for a field the diagnostic does not model is refused, not silently stored
    # (it would be shown beside the candidate although the model never reads it).
    cand_rules.check_features_known(spec.get("feature_columns"), features, name)
    change_case_rules.check_candidate_features_finite(features, name)
    # Same canonical-unit discipline as the historical data: where the case declares a unit the candidate must
    # state one and it is converted only by an exact factor; nothing is guessed. What was ENTERED is kept alongside.
    converted, input_record, problems = evidence_rules.convert_candidate_inputs(spec, features, units)
    if problems:
        raise ValidationError(f"Candidate {name!r} was not added: " + " ".join(problems), field="features")
    change_case_rules.check_candidate_features_finite(converted, name)
    return converted, (json.dumps(input_record) if (units or spec.get("units")) else None)


def add_candidate(organization_id: int, change_case_id: int, name: str, features: dict, units: dict = None) -> dict:
    case = get_change_case_or_404(organization_id, change_case_id)
    change_case_rules.check_can_add_candidate(case["status"])
    existing = [c["candidate_name"] for c in candidate_repo.list_candidates_for_change_case(
        organization_id, change_case_id, include_archived=True)]
    spec = json.loads(case["qualification_spec_json"])
    converted, input_json = validate_candidate_inputs(spec, name, features, units, existing)
    candidate_id = candidate_repo.create_candidate(
        organization_id, change_case_id, name, converted, input_json=input_json,
    )
    return candidate_repo.get_candidate(organization_id, candidate_id)


def _assess_sufficiency(spec: dict, dataset, experiments: list, candidates: list, ranking_status: dict = None) -> dict:
    rows = [{"features": json.loads(e["features_json"]), "target_value": e["target_value"]} for e in experiments]
    cands = [{"candidate_name": c["candidate_name"], "properties": json.loads(c["properties_json"])} for c in candidates]
    review = json.loads(dataset["review_json"]) if dataset and dataset.get("review_json") else None
    return change_case_rules.assess_evidence_sufficiency(
        spec, rows, cands, has_dataset=dataset is not None, dataset_review=review,
        ranking_status=ranking_status,
    )


def get_evidence_sufficiency(organization_id: int, change_case_id: int) -> dict:
    """Read-only: the same assessment the ranking gate enforces, for the
    review screen. Ownership-checked like every other change-case read."""
    case = get_change_case_or_404(organization_id, change_case_id)
    spec = json.loads(case["qualification_spec_json"])
    dataset = qualification_dataset_repo.get_latest_dataset_for_change_case(organization_id, change_case_id)
    experiments = (
        qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset["id"]) if dataset else []
    )
    candidates = candidate_repo.list_candidates_for_change_case(organization_id, change_case_id)
    # Are the rankings on screen based on the current evidence? (informational only)
    stale, has_predictions, filenames = [], False, {}
    for c in candidates:
        pred = candidate_repo.get_latest_prediction_for_candidate(organization_id, c["id"])
        if pred is None:
            continue
        has_predictions = True
        if dataset is not None and pred["dataset_version_id"] != dataset["id"]:
            if pred["dataset_version_id"] not in filenames:
                old = qualification_dataset_repo.get_dataset_for_change_case(
                    organization_id, change_case_id, pred["dataset_version_id"])
                filenames[pred["dataset_version_id"]] = old["original_filename"] if old else "an earlier dataset"
            stale.append({"candidate_name": c["candidate_name"], "from_filename": filenames[pred["dataset_version_id"]]})
    ranking_status = {
        "stale": stale, "has_predictions": has_predictions,
        "current_filename": dataset["original_filename"] if dataset else None,
    }
    result = _assess_sufficiency(spec, dataset, experiments, candidates, ranking_status)
    # Level 1D (informational, never blocks): do the rankings on screen still describe the candidates as
    # they are now? Candidate edits and additions/removals leave the old rankings as history only.
    views = list_candidates_with_predictions(organization_id, change_case_id)
    edited = [v["candidate_name"] for v in views if v.get("prediction_inputs_stale")]
    order_outdated = any(v.get("ranking_outdated") for v in views)
    if edited or order_outdated:
        detail = []
        if edited:
            detail.append("Input values were changed after the ranking ran for: " + ", ".join(edited) + ".")
        if order_outdated:
            detail.append(cand_rules.ORDER_OUTDATED_NOTICE)
        detail.append("Re-run ranking before relying on the order or the estimates.")
        check = change_case_rules._check(
            "RANKING_MATCHES_CANDIDATES", change_case_rules.SUFFICIENCY_WARN,
            "Rankings describe the current candidates", " ".join(detail),
        )
        result["checks"].append(check)
        result["warnings"].append(check)
    return result


def _historical_ranges(spec: dict, experiments: list):
    """{feature: (min, max)} from one dataset version's stored rows, or
    None if that dataset has no rows."""
    if not experiments:
        return None
    rows = [{"features": json.loads(e["features_json"])} for e in experiments]
    return change_case_rules.compute_historical_ranges(rows, spec["feature_columns"])


def _dataset_ref(dataset):
    return {"id": dataset["id"], "original_filename": dataset["original_filename"]} if dataset else None


def list_candidates_with_predictions(organization_id: int, change_case_id: int, include_archived: bool = False) -> list:
    """The diagnostic's candidates, each with its newest prediction and everything needed to read that
    prediction honestly: what dataset and which input values it was computed for, whether the ranking
    still describes the current candidates, and what may be done with the candidate.
    Candidates removed from the analysis (archived) are included only when asked for."""
    case = get_change_case_or_404(organization_id, change_case_id)
    spec = json.loads(case["qualification_spec_json"])
    feature_columns = list(spec.get("feature_columns") or [])
    everyone = candidate_repo.list_candidates_for_change_case(organization_id, change_case_id, include_archived=True)
    candidates = [c for c in everyone if include_archived or not c.get("archived_at")]
    usage_map = candidate_repo.get_candidate_usage_map(organization_id, change_case_id)
    ranges_by_dataset = {}
    # Stale-ranking marker: a prediction is STALE when the dataset it was generated from
    # is no longer the current (newest) dataset. Display-only: nothing below changes which
    # dataset anything is computed from.
    latest_dataset = qualification_dataset_repo.get_latest_dataset_for_change_case(organization_id, change_case_id)
    dataset_refs = {}
    predictions = {}

    def prediction_of(candidate):
        if candidate["id"] not in predictions:
            predictions[candidate["id"]] = candidate_repo.get_latest_prediction_for_candidate(
                organization_id, candidate["id"])
        return predictions[candidate["id"]]

    # Does the ranking still cover the candidates as they are now? It does not if candidates were added,
    # removed or restored after the latest run, or if any candidate's input values were edited since.
    active_all = [c for c in everyone if not c.get("archived_at")]
    archived_all = [c for c in everyone if c.get("archived_at")]
    set_changed = cand_rules.ranking_set_changed(
        [{"latest_prediction_created_at": (prediction_of(c) or {}).get("created_at"),
          "restored_at": c.get("restored_at")} for c in active_all],
        [{"archived_at": c["archived_at"],
          "prediction_count": (usage_map.get(c["id"]) or {}).get("prediction_count", 0)} for c in archived_all],
    )
    any_edited = any(cand_rules.prediction_matches_inputs(c, prediction_of(c)) is False for c in active_all)
    order_outdated = set_changed or any_edited

    for c in candidates:
        stored = json.loads(c["properties_json"])
        # Only the modelled input features are shown as the candidate's inputs. A value stored for any
        # other field (possible only in rows created before unknown fields were refused) is reported
        # separately and never presented as something the model used.
        c["properties"] = {k: v for k, v in stored.items() if k in feature_columns} if feature_columns else stored
        extra = {k: v for k, v in stored.items() if feature_columns and k not in feature_columns}
        c["ignored_properties"] = extra or None
        c["input_record"] = json.loads(c["input_json"]) if c.get("input_json") else None
        c.update(cand_rules.lifecycle_flags(c, usage_map.get(c["id"])))
        c["latest_prediction"] = prediction_of(c)
        c["domain_coverage"] = None
        c["decision_support"] = None  # C1: None until a prediction exists
        c["prediction_stale"] = None  # None until a prediction exists
        c["prediction_dataset"] = None
        c["current_dataset"] = _dataset_ref(latest_dataset)
        c["stale_notice"] = None
        c["prediction_inputs_stale"] = None
        c["ranking_outdated"] = None
        c["ranking_notice"] = None
        pred = c["latest_prediction"]
        if pred is None:
            continue
        if pred["dataset_version_id"] not in dataset_refs:
            dataset_refs[pred["dataset_version_id"]] = _dataset_ref(
                qualification_dataset_repo.get_dataset_for_change_case(
                    organization_id, change_case_id, pred["dataset_version_id"])
            )
        c["prediction_dataset"] = dataset_refs[pred["dataset_version_id"]]
        c["prediction_stale"] = latest_dataset is not None and pred["dataset_version_id"] != latest_dataset["id"]
        if c["prediction_stale"]:
            c["stale_notice"] = change_case_rules.stale_ranking_notice(
                (c["prediction_dataset"] or {}).get("original_filename", "an earlier dataset"),
                latest_dataset["original_filename"],
            )
        # Level 1D: was the candidate's input data edited after this prediction was computed? Then the
        # prediction is kept as history but describes the OLD values: coverage and decision support
        # computed from the NEW values must not be shown beside it, and it takes no part in the ranking.
        c["prediction_inputs_stale"] = not cand_rules.prediction_matches_inputs(c, pred)
        if c["prediction_inputs_stale"]:
            c["ranking_notice"] = cand_rules.INPUTS_CHANGED_NOTICE
            continue
        if not c["archived"]:
            c["ranking_outdated"] = order_outdated
            if order_outdated:
                c["ranking_notice"] = cand_rules.ORDER_OUTDATED_NOTICE
        # Domain coverage is judged against the dataset THIS prediction was
        # generated from (dataset_version_id) -- never the latest upload --
        # so a later, wider dataset can't quietly turn an extrapolation into
        # an in-range candidate.
        dataset_id = pred["dataset_version_id"]
        if dataset_id not in ranges_by_dataset:
            experiments = qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset_id)
            ranges_by_dataset[dataset_id] = _historical_ranges(spec, experiments)
        ranges = ranges_by_dataset[dataset_id]
        if ranges is not None:
            c["domain_coverage"] = change_case_rules.classify_candidate_domain_coverage(c["properties"], ranges)
        # C1: categorical decision support, derived ONLY from the domain
        # coverage above (so it inherits the same dataset-version
        # provenance). If a prediction exists but coverage could not be
        # determined (ranges is None), this fails safe to requires_validation.
        c["decision_support"] = change_case_rules.derive_decision_support(c["domain_coverage"], has_prediction=True)
    _assign_ranks(candidates, spec)
    return candidates


def _assign_ranks(candidates: list, spec) -> None:
    """Sets c["rank"], c["ranking_status"] and c["ranking_statement"] on every candidate whose prediction is
    for its CURRENT inputs, using the SAME evidence-aware rule as the ranking run and the report
    (ranking_evidence_rules.evidence_ranking) applied to the STORED predictions, so a page refresh shows
    exactly what the run reported.

    * ranking_status / ranking_statement say how well the historical data supports ranking the candidate
      (ranked / ranked_with_caution / not_ranked_insufficient_evidence). They are shown whenever a prediction
      for the current inputs exists, so an extrapolation is never hidden.
    * rank is a position (1 = best) only for a candidate the evidence supports, and only while the ranking
      still covers the candidates as they are now. A candidate outside the observed range has rank None;
      after an edit or a change of the candidate set no position is shown at all until the ranking is re-run.
    * A candidate removed from the analysis, or whose prediction is for previous input values, gets neither.
    The list itself stays in candidate-id order."""
    for c in candidates:
        c["rank"] = None
        c["ranking_status"] = None
        c["ranking_statement"] = None
    current = [
        c for c in candidates
        if c.get("latest_prediction") and not c.get("prediction_inputs_stale") and not c.get("archived")
    ]
    if not current:
        return
    verdicts = ranking_evidence_rules.evidence_ranking(
        [{"id": c["id"], "predicted_probability": c["latest_prediction"]["predicted_probability"],
          "predicted_value": c["latest_prediction"].get("predicted_value"),
          "decision_support": c.get("decision_support")} for c in current],
        (spec or {}).get("direction", "maximize"),
    )["by_id"]
    for c in current:
        verdict = verdicts[c["id"]]
        c["ranking_status"] = verdict["status"]
        c["ranking_statement"] = verdict["statement"]
        c["rank"] = None if c.get("ranking_outdated") else verdict["rank"]


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


def _compute_evidence_metrics(spec: dict, experiments: list) -> tuple:
    """Priority 7B: cross-validated model-quality and uncertainty-
    calibration metrics, recomputed here rather than persisted -- report
    generation does not re-run ranking, so this mirrors
    _assess_dataset_quality's own provenance discipline (computed fresh
    from the SAME dataset version the report's predictions came from,
    never a stored value) rather than duplicating rank_change_case's
    already-tested logic in place."""
    feature_columns = spec["feature_columns"]
    # C2: exact duplicate rows are counted by the data-quality check but must not
    # inflate the evidence the model is fit/scored on (a repeated point would make
    # its region look better-supported than the distinct evidence is).
    fit_rows = change_case_rules.deduplicate_rows([
        {"features": json.loads(e["features_json"]), "target_value": e["target_value"]} for e in experiments
    ])
    X = np.array([[r["features"][col] for col in feature_columns] for r in fit_rows])
    y = np.array([r["target_value"] for r in fit_rows])
    from engine.facade import compute_model_metrics, compute_uncertainty_calibration
    model_quality = compute_model_metrics(X, y)
    uncertainty_calibration = (
        compute_uncertainty_calibration(X, y) if "error" not in model_quality else None
    )
    return model_quality, uncertainty_calibration


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
    # Every modelled field and the target needs an explicit canonical unit. Checked first, before anything is
    # read or computed, so a refused ranking persists nothing and never looks like it ran.
    change_case_rules.check_units_declared_for_ranking(json.loads(case["qualification_spec_json"]))

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
        candidate_features = json.loads(c["properties_json"])
        change_case_rules.check_candidate_features_complete(
            feature_columns, candidate_features, c["candidate_name"],
        )
        # Priority 7A (B2), defense-in-depth: reject non-finite candidate
        # feature values here too, independent of add_candidate's own
        # check, so malformed data can never reach the engine however it
        # was stored.
        change_case_rules.check_candidate_features_finite(candidate_features, c["candidate_name"])

    # Priority 7A (B2), defense-in-depth: reject a non-finite historical
    # row here too, independent of ingestion's own check, so a row that
    # reached storage before this check existed (or by any other path)
    # can never crash the GP with a raw sklearn exception.
    change_case_rules.check_rows_are_finite([
        {"features": json.loads(e["features_json"]), "target_value": e["target_value"]}
        for e in experiments
    ])

    # Evidence-sufficiency gate (hard stop). Runs after the older, more specific
    # checks above so their messages are unchanged; it adds the explicit
    # explanation of what is missing and the rows-vs-features rule.
    sufficiency = _assess_sufficiency(spec, dataset, experiments, candidates)
    if not sufficiency["can_rank"]:
        raise ValidationError(
            "Evidence-sufficiency gate: ranking was stopped because the evidence cannot support a responsible "
            "analysis. " + " ".join(f"[{c['code']}] {c['detail']}" for c in sufficiency["blocking"]),
            field="evidence",
        )

    historical_ranges = _historical_ranges(spec, experiments)  # same dataset the predictions are stamped with

    # C2: fit and score on DISTINCT rows only (see _compute_evidence_metrics).
    # historical_ranges above deliberately still uses all rows: removing exact
    # duplicates cannot change a min/max.
    fit_rows = change_case_rules.deduplicate_rows([
        {"features": json.loads(e["features_json"]), "target_value": e["target_value"]} for e in experiments
    ])
    X = np.array([[r["features"][col] for col in feature_columns] for r in fit_rows])
    y = np.array([r["target_value"] for r in fit_rows])

    from engine.facade import compute_model_metrics, compute_uncertainty_calibration
    model_quality = compute_model_metrics(X, y)
    uncertainty_calibration = (
        compute_uncertainty_calibration(X, y) if "error" not in model_quality else None
    )

    # Candidates cross the engine boundary with their database id. Identity is never
    # reconstructed from names, so duplicate or look-alike names cannot attach a
    # prediction to the wrong candidate.
    engine_candidates = [
        {"id": c["id"], "name": c["candidate_name"], "features": json.loads(c["properties_json"])}
        for c in candidates
    ]

    ranked = engine_rank_candidates(X, y, direction, feature_columns, target_value, engine_candidates)

    candidate_ids = {c["id"] for c in candidates}
    # The inputs_version each candidate had when it was read: every prediction is stamped with it, so
    # a later edit of the candidate's values is recognised as "this prediction is for the old values".
    versions_read = {c["id"]: int(c.get("inputs_version") or 0) for c in candidates}
    ranked_ids = [r["id"] for r in ranked]
    if len(ranked_ids) != len(candidate_ids) or set(ranked_ids) != candidate_ids:
        raise RuntimeError(
            "Ranking returned a different set of candidates than was submitted; nothing was saved."
        )

    # Everything is computed first and saved afterwards, in ONE transaction
    # (candidate_repo.persist_ranking): a failure anywhere leaves no partial ranking.
    #
    # Positions are decided by the evidence-aware rule (ranking_evidence_rules): only candidates whose inputs
    # the historical data supports are ordered against each other. A candidate clearly outside the observed
    # range keeps the model's own numbers -- unchanged -- but gets NO position (neither first nor last).
    prepared = {}
    for r in ranked:
        domain_coverage = change_case_rules.classify_candidate_domain_coverage(r["features"], historical_ranges)
        prepared[r["id"]] = (domain_coverage, change_case_rules.derive_decision_support(domain_coverage, has_prediction=True))  # C1
    evidence = ranking_evidence_rules.evidence_ranking(
        [{"id": r["id"], "predicted_probability": r["predicted_probability"],
          "predicted_value": r["predicted_value"], "decision_support": prepared[r["id"]][1]} for r in ranked],
        direction,
    )
    by_id = {r["id"]: r for r in ranked}
    results = []
    to_persist = []
    for candidate_id in evidence["order"]:
        r = by_id[candidate_id]
        domain_coverage, decision_support = prepared[candidate_id]
        verdict = evidence["by_id"][candidate_id]
        experiment_description = _minimum_experiment_description(
            r, target_value, direction, domain_coverage, ranking_status=verdict["status"])
        to_persist.append({
            "candidate_id": candidate_id,
            "inputs_version": versions_read[candidate_id],
            "model_version": r["model_version"],
            "predicted_value": r["predicted_value"],
            "predicted_probability": r["predicted_probability"],
            "uncertainty_std": r["uncertainty_std"],
            "experiment_description": experiment_description,
            "priority": 1,
        })
        results.append({
            "rank": verdict["rank"],  # None = not ranked (insufficient evidence); never first, never last
            "ranking_status": verdict["status"],
            "ranking_statement": verdict["statement"],
            "ranking_summary": evidence["summary"],
            "candidate_id": candidate_id,
            "candidate_name": r["name"],
            "predicted_value": r["predicted_value"],
            "predicted_probability": r["predicted_probability"],
            "uncertainty_std": r["uncertainty_std"],
            "recommended_experiment": experiment_description,
            "domain_coverage": domain_coverage,
            "decision_support": decision_support,
            # Priority 7B: previously computed above and discarded. Same
            # values for every candidate in this ranking call (they describe
            # the dataset/model, not an individual candidate) -- attached
            # per-result to match the existing shape (domain_coverage is
            # likewise dataset-derived but repeated per-candidate).
            "model_quality": model_quality,
            "uncertainty_calibration": uncertainty_calibration,
        })

    try:
        candidate_repo.persist_ranking(organization_id, dataset["id"], to_persist)
    except candidate_repo.RankingConflict:
        raise ValidationError(
            "A candidate was changed while the ranking was running, so nothing was saved. "
            "Run the ranking again.",
            field="candidates",
        ) from None
    return results


def _minimum_experiment_description(ranked_candidate: dict, target_value: float, direction: str,
                                     domain_coverage: dict = None, ranking_status: str = None) -> str:
    """A simple, honest, rule-based minimum-validation-experiment
    description -- deliberately NOT sophisticated active-learning
    experiment design in this phase (that's flagged as a known,
    accepted limitation, not built here). States plainly that this is
    the recommended NEXT physical validation step, not a claim that
    physical qualification itself is complete or fast."""
    prob = ranked_candidate["predicted_probability"]
    if ranking_status == ranking_evidence_rules.STATUS_NOT_RANKED:
        # The historical data does not cover this candidate, so the model's number says nothing reliable about
        # it in EITHER direction: no "test this first" and no "not worth testing" -- only that physical
        # validation is what can settle it.
        description = (
            f"Run a physical qualification test against the target spec for '{ranked_candidate['name']}'. "
            f"The historical data does not cover this candidate's inputs, so the model cannot say whether it is "
            f"likely to meet the requirement: its estimate ({change_case_rules.format_model_probability(prob)}) is an extrapolation and is not used to "
            f"rank it or to rule it in or out. Only physical validation can establish how it performs -- this "
            f"analysis does not replace running and recording the actual physical result."
        )
    else:
        if prob >= 0.8:
            confidence_note = "high model-estimated probability"
        elif prob >= 0.4:
            confidence_note = "moderate model-estimated probability -- validation is important before relying on this candidate"
        else:
            confidence_note = "low model-estimated probability -- the model rates this candidate below the others, but its estimates can be wrong in either direction; only physical validation can show how it performs"
        description = (
            f"Run a physical qualification test against the target spec for "
            f"'{ranked_candidate['name']}' ({confidence_note}; model-estimated probability "
            f"{change_case_rules.format_model_probability(prob)}, a model output rather than a measured or validated "
            f"result). This analysis identifies which "
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


def _outdated_ranking_message(case: dict, edited_names: list) -> str:
    parts = ["The report can't be generated yet: the last ranking no longer matches the current candidates."]
    if edited_names:
        parts.append("Input values were changed after the ranking ran for: " + ", ".join(edited_names) + ".")
    if case.get("archived_at"):
        parts.append("This diagnostic is archived and can't be re-ranked: restore it first.")
    parts.append(cand_rules.ORDER_OUTDATED_NOTICE)
    return " ".join(parts)


def generate_report(organization_id: int, change_case_id: int, organization_name: str) -> str:
    """Builds the qualification diagnostic .docx from the change case's
    latest candidates/predictions -- the commercial deliverable. Uses
    whatever ranking results currently exist (does not re-run ranking);
    call rank_change_case first if a fresh ranking is wanted."""
    from backend.app.services import report_builder

    case = get_change_case_or_404(organization_id, change_case_id)
    candidates = list_candidates_with_predictions(organization_id, change_case_id)

    # The report presents ONE ranking as the current comparison of the candidates, so that ranking must still
    # describe the candidates as they are now. If a candidate was added, removed, restored or edited after the
    # ranking ran, the positions come from a different set: printing them (or silently dropping the changed
    # candidates and ranking whoever is left) would show an earlier comparison as the current one. The
    # candidate listing already withholds those positions; the report is refused for the same condition, the
    # same way it is already refused when predictions come from different dataset versions (below).
    edited = [c["candidate_name"] for c in candidates if c.get("prediction_inputs_stale")]
    if edited or any(c.get("ranking_outdated") for c in candidates):
        raise ValidationError(_outdated_ranking_message(case, edited), field="ranking")

    # Dataset provenance: a report is evaluated against the dataset its
    # predictions were generated from (predictions.dataset_version_id) --
    # NEVER silently against a newer upload. Only when no predictions exist
    # yet (so there is no provenance to preserve) does it fall back to the
    # latest dataset, which is then simply the data basis of an empty report.
    # A prediction computed for input values the candidate no longer has is history, not a result for the
    # candidate as it is now: it takes no part in the report's ranking (the candidate is listed as not
    # yet ranked).
    prediction_dataset_ids = {
        c["latest_prediction"]["dataset_version_id"]
        for c in candidates if c.get("latest_prediction") and not c.get("prediction_inputs_stale")
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
    model_quality = None
    uncertainty_calibration = None
    if dataset:
        experiments = qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset["id"])
        spec = json.loads(case["qualification_spec_json"])
        data_quality = _assess_dataset_quality(spec, dataset, experiments)
        change_case_rules.check_can_generate_report(data_quality["status"])
        if data_quality["status"] != "insufficient":
            # Priority 7B: only compute evidence metrics when the report is
            # actually claiming a model basis (the "insufficient" branch
            # already states INSUFFICIENT_EVIDENCE_STATEMENT and skips any
            # model claim, so showing calibration numbers there would be
            # contradictory, not additive).
            model_quality, uncertainty_calibration = _compute_evidence_metrics(spec, experiments)

    rec_spec = json.loads(case["qualification_spec_json"])
    current = [
        c for c in candidates
        if c.get("latest_prediction") and not c.get("prediction_inputs_stale")
    ]
    # Same evidence-aware rule as the ranking run and the candidate listing, so the table can never show a
    # different order -- or rank a candidate the historical data does not support ranking.
    evidence = ranking_evidence_rules.evidence_ranking(
        [{"id": c["id"], "predicted_probability": c["latest_prediction"]["predicted_probability"],
          "predicted_value": c["latest_prediction"].get("predicted_value"),
          "decision_support": c.get("decision_support")} for c in current],
        rec_spec["direction"],
    )
    by_id = {c["id"]: c for c in current}
    ranked_results = []
    for candidate_id in evidence["order"]:  # ranked first (best first), then the unranked by id
        c = by_id[candidate_id]
        pred = c["latest_prediction"]
        verdict = evidence["by_id"][candidate_id]
        # The recommendation text is built from THIS candidate's latest prediction and the domain coverage
        # shown beside it, by the same function that wrote the stored recommendation when the ranking ran --
        # so it can never quote a different ranking than the table it sits under.
        # (It used to read the stored recommendations and take the OLDEST: every ranking run stores
        # a new one with the same priority, and that listing is ordered by id ASC.)
        description = _minimum_experiment_description(
            {"name": c["candidate_name"], "predicted_probability": pred["predicted_probability"]},
            rec_spec["target_value"], rec_spec["direction"], c.get("domain_coverage"),
            ranking_status=verdict["status"],
        )
        if c.get("outcome_count"):
            # Don't ask for a test the report itself shows as already done without saying so: the recorded
            # result, not this estimate, is the evidence of how the candidate performed.
            description += (
                " A physical outcome has already been recorded for this candidate (see 'Recorded physical "
                "validation outcomes'); that recorded result, not this model estimate, is the evidence of how "
                "it performed."
            )
        ranked_results.append({
            "rank": verdict["rank"],  # None = not ranked (insufficient evidence)
            "ranking_status": verdict["status"],
            "ranking_statement": verdict["statement"],
            "candidate_id": c["id"],
            "candidate_name": c["candidate_name"],
            "predicted_value": pred.get("predicted_value"),  # None = not recorded for this (older) prediction
            "predicted_probability": pred["predicted_probability"],
            "uncertainty_std": pred["uncertainty_std"],
            "recommended_experiment": description,
            "domain_coverage": c.get("domain_coverage"),
            "decision_support": c.get("decision_support"),  # C1
        })

    audit_context = _build_report_audit_context(
        organization_id, change_case_id, case, candidates, dataset,
    )
    return report_builder.build_qualification_report(
        case, ranked_results, n_historical_rows, organization_name, data_quality=data_quality,
        model_quality=model_quality, uncertainty_calibration=uncertainty_calibration,
        audit_context=audit_context,
    )


def _utc(stamp: str):
    from datetime import datetime, timezone
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _estimate_before_outcome(organization_id: int, candidate, outcome: dict):
    """(prediction, None) for the newest estimate that existed when `outcome` was recorded and was computed for
    the candidate's CURRENT input values, else (None, reason).

    Ranking is re-run, never overwritten, so earlier predictions are still stored. Pairing a recorded physical
    result with the candidate's LATEST estimate would imply the model had predicted a test that had already
    happened. Nothing stored says which estimate (if any) motivated a given test -- an outcome's
    recommended_experiment_id is optional and a recommended experiment carries no prediction id -- so this only
    establishes what existed beforehand, and the report says exactly that. A candidate's inputs cannot be
    changed once an outcome is recorded for it, so the current inputs are the inputs that were tested."""
    if candidate is None:
        return None, "Not compared: the candidate could not be found."
    recorded_at = _utc(outcome["created_at"])
    earlier = [
        p for p in candidate_repo.list_predictions_for_candidate(organization_id, candidate["id"])
        if cand_rules.prediction_matches_inputs(candidate, p) and _utc(p["created_at"]) <= recorded_at
    ]
    if not earlier:
        return None, ("Not compared: no model estimate for this candidate's inputs existed when this result "
                      "was recorded.")
    return earlier[-1], None


def _build_report_audit_context(organization_id: int, change_case_id: int, case: dict,
                                 candidates: list, dataset) -> dict:
    """Read-only facts for the report's requirement/analysis record, evidence
    gaps and recorded-outcomes sections. Everything here is already stored
    (spec, dataset row, prediction rows, candidate inputs, outcome rows);
    nothing new is computed or persisted, and no threshold is introduced.
    Outcomes are shown NEXT TO the model estimate for comparison only."""
    from datetime import datetime, timezone
    spec = json.loads(case["qualification_spec_json"])
    current = [c for c in candidates if c.get("latest_prediction") and not c.get("prediction_inputs_stale")]
    predictions = [c["latest_prediction"] for c in current]
    # Outcomes recorded for a candidate that was later removed from the analysis keep its name.
    all_candidates = {
        c["id"]: c
        for c in candidate_repo.list_candidates_for_change_case(organization_id, change_case_id, include_archived=True)
    }
    outcomes = []
    for o in candidate_repo.list_outcomes_for_change_case(organization_id, change_case_id):
        candidate = all_candidates.get(o["candidate_id"])
        estimate, note = _estimate_before_outcome(organization_id, candidate, o)
        outcomes.append({
            "candidate_name": candidate["candidate_name"] if candidate else "Unknown candidate",
            # Only an estimate that already existed when the result was recorded -- never a newer one.
            "model_probability": estimate["predicted_probability"] if estimate else None,
            "estimate_made_at": estimate["created_at"] if estimate else None,
            "comparison_note": note,
            "actual_result": o["actual_result"],
            "passed_spec": bool(o["passed_spec"]),
            "recorded_at": o["created_at"],
        })
    return {
        "change_case_id": change_case_id,
        "spec": spec,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": dataset,
        "stale_notice": next((c["stale_notice"] for c in candidates if c.get("stale_notice")), None),
        "ingestion_review": (
            json.loads(dataset["review_json"]) if dataset and dataset.get("review_json") else None
        ),
        "model_versions": sorted({p["model_version"] for p in predictions}),
        "ranked_at_utc": max((p["created_at"] for p in predictions), default=None),
        "candidates": [
            {
                "candidate_name": c["candidate_name"],
                "properties": c["properties"],
                "has_prediction": bool(c.get("latest_prediction")) and not c.get("prediction_inputs_stale"),
                "input_record": c.get("input_record"),
            }
            for c in candidates
        ],
        "outcomes": outcomes,
    }