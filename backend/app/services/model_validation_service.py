"""Model registry, validation protocols, applicability and comparison.

A model version is the identity of what was fitted: the canonical training rows, feature definitions, target,
preprocessing, configuration, seed and engine version. The Gaussian process itself is refitted whenever a ranking
runs, so a version records what that fit is made from; it does not store fitted parameters.

Nothing here approves a model. A validation record says which protocol ran, on how many rows, and how it ended."""
import json
import logging

import numpy as np

from backend.app.repositories import (change_case_repo, model_registry_repo as repo, program_workspace_repo as workspace_repo,
                                      qualification_dataset_repo)
from backend.app.schemas.errors import Forbidden, ValidationError
from backend.app.services import change_case_rules, change_case_service
from backend.app.services import program_workspace_rules as workspace_rules
from engine import facade as engine

log = logging.getLogger(__name__)

SEED = 0  # the seed ranking uses; a version records it
MAX_SYNC_ROWS = 800  # validation runs inside the request until background jobs exist
RUN_MESSAGE = "Only an owner, an admin, the program owner or a reviewer can run validation."
MIN_COMPARE_SCORED = 10


# ------------------------------------------------------------------ training data
def _case(organization_id: int, case_id: int) -> dict:
    case = change_case_repo.get_change_case(organization_id, case_id)
    if case is None:
        raise LookupError("Program not found.")
    return case


def _training(organization_id: int, case: dict, dataset: dict) -> dict:
    """The rows a model is fitted on, exactly as ranking selects them: this dataset's stored rows with exact
    duplicates removed. Returned in canonical order, so identity and every evaluation are order independent."""
    spec = json.loads(case["qualification_spec_json"])
    columns = list(spec["feature_columns"])
    experiments = qualification_dataset_repo.list_experiments_for_dataset(organization_id, dataset["id"])
    fit_rows = change_case_rules.deduplicate_rows(
        [{"features": json.loads(e["features_json"]), "target_value": e["target_value"]} for e in experiments])
    names = sorted(columns)
    keyed = sorted(range(len(fit_rows)), key=lambda i: engine.identity.canonical_rows([fit_rows[i]], columns)[0])
    fit_rows = [fit_rows[i] for i in keyed]
    X = np.array([[r["features"][c] for c in columns] for r in fit_rows], dtype=float).reshape(len(fit_rows), len(columns))
    y = np.array([r["target_value"] for r in fit_rows], dtype=float)
    groups = change_case_service._replicate_groups(fit_rows, columns)
    units = spec.get("units") or {}
    target = {"name": spec.get("target_metric"), "unit": units.get(spec.get("target_metric")),
              "direction": spec.get("direction", "maximize")}
    return {"spec": spec, "columns": columns, "sorted_columns": names, "rows": fit_rows, "X": X, "y": y, "groups": groups,
            "units": units, "target": target, "n_experiments": len(experiments)}


def _identity(training: dict) -> dict:
    cols = training["columns"]
    return engine.identity.model_identity(
        training["rows"], cols, training["units"], training["target"], engine.identity.describe_preprocessing(),
        engine.identity.describe_surrogate(len(cols), SEED), SEED)


def register(organization_id: int, case: dict, dataset: dict, user_id, source: str) -> tuple:
    """Registers (or finds) the model version for this dataset. Same identity, same record."""
    training = _training(organization_id, case, dataset)
    if len(training["rows"]) < 2:
        raise ValidationError("There is not enough evidence to register a model version.", field="dataset")
    identity = _identity(training)
    existing = repo.get_by_identity(organization_id, case["id"], identity["identity_hash"])
    if existing:
        return existing, False
    summary = {
        "data": engine.uncertainty.data_summary(training["X"], training["y"], training["columns"], training["groups"]),
        "replicate_scatter": engine.uncertainty.replicate_scatter(training["y"], training["groups"]),
        "fit": engine.uncertainty.fitted_noise_summary(training["X"], training["y"], SEED),
        "training_data_hash": identity["training_data_hash"],
        "dataset_filename": dataset.get("original_filename"),
    }
    payload = identity["payload"]
    return repo.create_version(
        organization_id, case["id"], identity, dataset["id"], payload["features"], payload["target"], payload["preprocessing"],
        payload["configuration"], payload["runtime"], summary, SEED, engine.ENGINE_VERSION,
        engine.MODEL_FAMILY, len(training["rows"]), len(set(training["groups"])), source, user_id)


def register_for_ranking(organization_id: int, case: dict, dataset: dict, user_id):
    """Called by ranking. A registration problem must never block a ranking that otherwise succeeds; the predictions
    are then saved without a registered model, which every screen says plainly."""
    try:
        return register(organization_id, case, dataset, user_id, "ranking")[0]
    except Exception:  # noqa: BLE001
        log.exception("model version could not be registered for case %s", case.get("id"))
        return None


# ------------------------------------------------------------------ permissions
def _may_run(organization_id: int, role: str, user_id: int, case: dict) -> bool:
    return (role in workspace_rules.DECISION_ROLES or case.get("owner_user_id") == user_id
            or workspace_repo.is_reviewer(organization_id, case["id"], user_id))


def _check_run(organization_id, role, user_id, case):
    if not _may_run(organization_id, role, user_id, case):
        raise Forbidden(RUN_MESSAGE)
    change_case_service._check_entitled(organization_id)


def _writable(case: dict) -> None:
    from backend.app.services import change_case_lifecycle_rules as lifecycle_rules
    lifecycle_rules.check_not_archived(case)
    if case["status"] == "completed":
        raise ValidationError("This program is closed and can no longer be changed.", field="status")


# ------------------------------------------------------------------ register current evidence
def register_current(organization_id: int, role: str, user_id: int, case_id: int) -> dict:
    case = _case(organization_id, case_id)
    _check_run(organization_id, role, user_id, case)
    _writable(case)
    dataset = qualification_dataset_repo.get_latest_dataset_for_change_case(organization_id, case_id)
    if dataset is None:
        raise ValidationError("Upload and accept evidence before registering a model version.", field="dataset")
    from backend.app.repositories import evidence_intel_repo
    held = evidence_intel_repo.newest_held_after(organization_id, case_id, dataset["id"])
    if held:
        raise ValidationError(f"{held['original_filename']} is under review. Resolve it before registering a model version.", field="dataset")
    version, created = register(organization_id, case, dataset, user_id, "registered")
    return {"created": created, "version": summarise_version(organization_id, version)}


# ------------------------------------------------------------------ validation
def latest_by_protocol(validations: list) -> dict:
    out = {}
    for v in validations:  # newest first
        out.setdefault(v["protocol_key"], v)
    return out


def _state(validations: list) -> dict:
    return engine.validation_runner.summarise({k: v["result"] for k, v in latest_by_protocol(validations).items()})


def _dataset_for_version(organization_id: int, version: dict):
    if version.get("dataset_id") is None:
        return None
    from backend.app.repositories import qualification_dataset_repo as q
    return q.get_dataset_for_change_case(organization_id, version["change_case_id"], version["dataset_id"])


def _runtime_matches(version: dict) -> bool:
    return version["environment"] == engine.identity.environment_record()


def _verified_training(organization_id: int, version: dict):
    """The training rows, only if they still reproduce the stored training data identity."""
    case = _case(organization_id, version["change_case_id"])
    dataset = _dataset_for_version(organization_id, version)
    if dataset is None:
        return None, "The evidence this version was built from is no longer stored."
    training = _training(organization_id, case, dataset)
    if not training["rows"] or _identity(training)["training_data_hash"] != version["training_data_hash"]:
        return None, "The stored evidence no longer reproduces this model version's identity."
    return training, None


def run_validation(organization_id: int, role: str, user_id: int, version_id: int, protocol_key: str) -> dict:
    version = repo.get_version(organization_id, version_id)
    if version is None:
        raise LookupError("Model version not found.")
    if protocol_key not in engine.validation_protocols.DEFINITIONS:
        raise ValidationError("Choose a listed validation protocol.", field="protocol")
    case = _case(organization_id, version["change_case_id"])
    _check_run(organization_id, role, user_id, case)
    _writable(case)
    protocol_hash = engine.validation_protocols.protocol_hash(protocol_key)
    previous = latest_by_protocol(repo.list_validations(organization_id, version_id)).get(protocol_key)
    if (previous and previous["status"] != "failed" and previous["protocol_hash"] == protocol_hash
            and previous["engine_version"] == engine.ENGINE_VERSION and previous["seed"] == SEED):
        return {"created": False, "validation": previous}  # the same protocol on the same data gives the same answer
    training, problem = _verified_training(organization_id, version)
    if training is None:
        raise ValidationError(problem, field="model")
    if not _runtime_matches(version):
        raise ValidationError("This version was built under different library versions than the ones running now. Register "
                              "a new version under the current libraries and validate that one.", field="model")
    if len(training["rows"]) > MAX_SYNC_ROWS:
        raise ValidationError(f"This evidence has {len(training['rows'])} rows. Validation runs while you wait and is limited to "
                              f"{MAX_SYNC_ROWS} rows until background processing is available.", field="model")
    result = engine.run_validation_protocol(protocol_key, training["X"], training["y"], groups=training["groups"], seed=SEED)
    record = repo.record_validation(organization_id, version, protocol_key, result, SEED, engine.ENGINE_VERSION, user_id)
    return {"created": True, "validation": record}


# ------------------------------------------------------------------ views
def _person_label(user_id):
    return None  # people are shown on the program, not repeated here


def summarise_version(organization_id: int, version: dict) -> dict:
    validations = repo.list_validations(organization_id, version["id"])
    latest = None
    dataset = _dataset_for_version(organization_id, version)
    current = qualification_dataset_repo.get_latest_dataset_for_change_case(organization_id, version["change_case_id"])
    return {
        "id": version["id"], "program_id": version["change_case_id"], "program_name": version.get("program_name"),
        "version_number": version["version_number"], "model_key": version["model_key"], "family": version["family"],
        "registered_at": version["created_at"], "source": version["source"], "kind": "build_record", "fitted_model_stored": False, "n_training_rows": version["n_training_rows"],
        "n_input_sets": version["n_input_sets"], "dataset_id": version["dataset_id"],
        "dataset_filename": dataset["original_filename"] if dataset else (version["training_summary"] or {}).get("dataset_filename"),
        "is_current": bool(current and version["dataset_id"] == current["id"]),
        "validation": _state(validations),
    }


def library(organization_id: int, program_id=None, limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    if program_id is not None and change_case_repo.get_change_case(organization_id, program_id) is None:
        raise LookupError("Program not found.")
    versions = repo.list_versions(organization_id, program_id, limit, offset)
    return {"total": repo.count_versions(organization_id, program_id), "limit": limit, "offset": offset,
            "versions": [summarise_version(organization_id, v) for v in versions]}


def protocols() -> list:
    return engine.validation_protocols.listing()


def detail(organization_id: int, role: str, user_id: int, version_id: int) -> dict:
    version = repo.get_version(organization_id, version_id)
    if version is None:
        raise LookupError("Model version not found.")
    case = _case(organization_id, version["change_case_id"])
    validations = repo.list_validations(organization_id, version_id)
    latest = latest_by_protocol(validations)
    _, problem = _verified_training(organization_id, version)
    base = summarise_version(organization_id, {**version, "program_name": case["name"]})
    return {
        **base,
        "identity_hash": version["identity_hash"], "training_data_hash": version["training_data_hash"],
        "engine_version": version["engine_version"], "seed": version["seed"],
        "feature_definitions": version["feature_definitions"], "target": version["target"],
        "preprocessing": version["preprocessing"], "configuration": version["configuration"],
        "environment": version["environment"], "training_summary": version["training_summary"],
        "kind": "build_record", "fitted_model_stored": False,
        "reproducibility": {"statement": BUILD_RECORD_STATEMENT, "runtime_recorded": version["environment"],
                            "runtime_now": engine.identity.environment_record(), "runtime_matches": _runtime_matches(version)},
        "integrity": {"reproduces_identity": problem is None, "problem": problem},
        "prediction_count": repo.prediction_count_for_version(organization_id, version_id),
        "protocols": [{**p, "latest": _brief(latest.get(p["key"]))} for p in protocols()],
        "history": [_brief(v) for v in validations],
        "permissions": {"can_run": _may_run(organization_id, role, user_id, case)},
        "limitations": LIMITATIONS,
    }


def _brief(v):
    if v is None:
        return None
    return {"id": v["id"], "protocol_key": v["protocol_key"], "protocol_hash": v["protocol_hash"], "status": v["status"],
            "message": v["message"], "n_rows": v["n_rows"], "n_scored": v["n_scored"], "created_at": v["created_at"],
            "seed": v["seed"], "engine_version": v["engine_version"]}


def validation_detail(organization_id: int, version_id: int, validation_id: int) -> dict:
    record = repo.get_validation(organization_id, validation_id)
    if record is None or record["model_version_id"] != version_id:
        raise LookupError("Validation record not found.")
    return {**_brief(record), "result": record["result"], "evaluation_rows_hash": record["evaluation_rows_hash"],
            "fold_assignment_hash": record["fold_assignment_hash"]}


BUILD_RECORD_STATEMENT = (
    "A model version is a build record, not a stored fitted model. It records the training rows, features, target, "
    "preprocessing, configuration, seed, engine version and library versions. The Gaussian process itself is not saved: it "
    "is refitted from the training rows at each ranking and each validation run. Refitting under the same recorded "
    "libraries is expected to reproduce the results. Under other library versions results can differ slightly, so a "
    "different runtime is registered as a different version.")

LIMITATIONS = [
    BUILD_RECORD_STATEMENT,
    "Validation shows how the model predicted held out rows from this evidence. It does not show how it will predict a new "
    "batch, supplier or material, and it is not a physical qualification result.",
    "The predicted probability shown with a candidate comes from the model's own normal prediction. It is not a calibrated "
    "probability.",
]


# ------------------------------------------------------------------ uncertainty
def uncertainty_view(organization_id: int, version_id: int) -> dict:
    version = repo.get_version(organization_id, version_id)
    if version is None:
        raise LookupError("Model version not found.")
    validations = latest_by_protocol(repo.list_validations(organization_id, version_id))
    grouped = (validations.get(engine.validation_protocols.GROUPED_INPUT_SETS) or {}).get("result")
    usable = grouped if grouped and grouped["status"] in ("completed", "completed_with_warnings") else None
    summary = version["training_summary"]
    fit = summary.get("fit") or {}
    preds = repo.predictions_for_version(organization_id, version_id)
    sigmas = [p["uncertainty_std"] for p in preds if p.get("uncertainty_std") is not None]
    types = engine.uncertainty.TYPES
    return {
        "model": {"description": types["model"], "status": "available" if sigmas else "no_predictions",
                  "predictive_sd_of_current_predictions": ({"min": min(sigmas), "max": max(sigmas), "n": len(sigmas)} if sigmas else None),
                  "fitted_noise_sd": fit.get("fitted_noise_sd"), "fit_status": fit.get("status"), "fit_note": fit.get("note"),
                  "held_out": ({"protocol": "grouped_input_sets_kfold", "n_scored": usable["counts"]["n_scored"],
                                "calibration": usable["calibration"]} if usable else None)},
        "measurement": {"description": types["measurement"], "status": "not_assessed",
                        "reason": "No measurement uncertainty is recorded with this evidence. Repeated rows are shown under data "
                                  "as observed scatter, which is not the same thing."},
        "data": {"description": types["data"], "status": "available", "summary": summary["data"],
                 "replicate_scatter": summary["replicate_scatter"]},
        "applicability": {"description": types["applicability"], "status": "see_applicability_tab",
                          "convention": engine.applicability.CONVENTION_NOTE},
        "note": "These are four different things. They are shown separately and are never added together.",
    }


# ------------------------------------------------------------------ applicability
def applicability_view(organization_id: int, version_id: int) -> dict:
    version = repo.get_version(organization_id, version_id)
    if version is None:
        raise LookupError("Model version not found.")
    training, problem = _verified_training(organization_id, version)
    out = {"convention": engine.applicability.CONVENTION_NOTE, "problem": problem, "candidates": [], "reference": None}
    if training is None:
        return out
    X, columns = training["X"], training["columns"]
    reference = engine.applicability.build_reference(X)
    lo, hi, limits = reference["lo"], reference["hi"], reference["limits"]
    out["reference"] = limits or {"status": "not_assessed",
                                  "reason": f"Fewer than {engine.applicability.MIN_REFERENCE_POINTS} distinct training input sets."}
    ranges = {c: (float(lo[i]), float(hi[i])) for i, c in enumerate(columns)}
    for p in repo.predictions_for_version(organization_id, version_id):
        props = json.loads(p["properties_json"])
        item = {"candidate_id": p["candidate_id"], "name": p["candidate_name"], "archived": bool(p.get("archived_at")),
                "predicted_value": p["predicted_value"], "uncertainty_std": p["uncertainty_std"], "features": props}
        if int(p.get("candidate_inputs_version") or 0) != int(p.get("inputs_version") or 0):
            item.update({"support": "not_assessed", "class": "not_assessed",
                         "reason": "The candidate was edited after this prediction, so the prediction no longer describes its current inputs."})
            out["candidates"].append(item)
            continue
        try:
            coverage = change_case_rules.classify_candidate_domain_coverage(props, ranges)
            x_raw = np.array([props[c] for c in columns], dtype=float)
            found = engine.applicability.assess_candidate(x_raw, X, reference)
            distance, nearest_vec, nearest_rows = found["nearest_distance"], found["nearest_input"], found["nearest_rows"]
        except (KeyError, TypeError, ValueError):
            item.update({"support": "not_assessed", "class": "not_assessed", "reason": "The candidate's inputs are incomplete."})
            out["candidates"].append(item)
            continue
        cls = found["class"]
        nearest = {c: float(nearest_vec[i]) for i, c in enumerate(columns)}
        range_status = coverage["status"]
        outside = range_status == change_case_rules.DOMAIN_OUTSIDE or any(f["status"] == change_case_rules.DOMAIN_OUTSIDE for f in coverage["features"].values())
        if outside or cls == engine.applicability.ISOLATED:
            support = "unsupported"
        elif cls == engine.applicability.SPARSE or range_status != change_case_rules.DOMAIN_WITHIN:
            support = "weak"
        elif cls == engine.applicability.NOT_ASSESSED:
            support = "not_assessed"
        else:
            support = "within_evidence"
        combination = (not outside) and range_status == change_case_rules.DOMAIN_WITHIN and cls in (engine.applicability.SPARSE, engine.applicability.ISOLATED)
        item.update({"range_status": range_status, "range_features": coverage["features"], "nearest_distance": distance, "class": cls,
                     "nearest_training_input_set": nearest, "nearest_training_rows": nearest_rows, "support": support, "unsupported_combination": combination})
        out["candidates"].append(item)
    out["support_note"] = ("Support combines a per feature range check with distance from the nearest training input set. It "
                           "describes where the evidence has data. It does not say a prediction is right or wrong.")
    return out


# ------------------------------------------------------------------ candidate decision support
def _not_assessed(model_version_id, reason, version_number=None, **extra):
    return {"status": "not_assessed", "class": engine.applicability.NOT_ASSESSED, "reason": reason,
            "nearest_distance": None, "thresholds": None, "nearest_training_input_set": None, "nearest_training_rows": None,
            "model_version_id": model_version_id, "model_version_number": version_number, **extra}


def _version_context(organization_id: int, case: dict, version: dict, cache: dict):
    """(training rows, reference) for one model version, or (None, reason). Cached for one request."""
    if version["id"] in cache:
        return cache[version["id"]]
    training, problem = _verified_training(organization_id, version)
    result = (None, problem) if training is None else ((training, engine.applicability.build_reference(training["X"])), None)
    cache[version["id"]] = result
    return result


def candidate_input_space(organization_id: int, case: dict, model_version_id, prediction_dataset_id, props: dict, cache: dict) -> dict:
    """Multivariate applicability of one candidate: nearest distinct training input set, measured against the training
    evidence of the model version that made its prediction, and never against any other evidence. Uses the same
    scaling, thresholds and classes as the Applicability tab. Anything that cannot be assessed says so, with the reason."""
    if model_version_id is None:
        return _not_assessed(None, "This prediction has no registered model version.")
    version = repo.get_version(organization_id, model_version_id)
    if version is None or version["change_case_id"] != case["id"]:
        return _not_assessed(model_version_id, "The model version for this prediction was not found.")
    number = version["version_number"]
    if version["dataset_id"] != prediction_dataset_id:
        return _not_assessed(model_version_id, "The model version was built from different evidence than this prediction used.", number)
    ctx, problem = _version_context(organization_id, case, version, cache)
    if ctx is None:
        return _not_assessed(model_version_id, problem, number)
    training, reference = ctx
    try:
        x_raw = np.array([props[c] for c in training["columns"]], dtype=float)
        if not np.isfinite(x_raw).all():
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return _not_assessed(model_version_id, "The candidate's inputs are incomplete.", number)
    found = engine.applicability.assess_candidate(x_raw, training["X"], reference)
    limits = reference["limits"]
    base = {"model_version_id": model_version_id, "model_version_number": number,
            "nearest_distance": found["nearest_distance"], "nearest_training_rows": found["nearest_rows"],
            "nearest_training_input_set": {c: float(found["nearest_input"][i]) for i, c in enumerate(training["columns"])},
            "thresholds": limits}
    if limits is None:
        return {**base, "status": "not_assessed", "class": engine.applicability.NOT_ASSESSED,
                "reason": f"Only {reference['n_input_sets']} distinct training input sets. At least {engine.applicability.MIN_REFERENCE_POINTS} are needed to judge spacing."}
    return {**base, "status": "assessed", "class": found["class"], "reason": None}


# ------------------------------------------------------------------ comparison
def compare(organization_id: int, a_id: int, b_id: int) -> dict:
    if a_id == b_id:
        raise ValidationError("Choose two different model versions.", field="b")
    a, b = repo.get_version(organization_id, a_id), repo.get_version(organization_id, b_id)
    if a is None or b is None:
        raise LookupError("Model version not found.")
    if a["change_case_id"] != b["change_case_id"]:
        raise ValidationError("Model versions can only be compared within one program.", field="b")
    la = latest_by_protocol(repo.list_validations(organization_id, a_id))
    lb = latest_by_protocol(repo.list_validations(organization_id, b_id))
    rows = []
    for key in engine.validation_protocols.DEFINITIONS:
        if key == engine.validation_protocols.RECORDED_UNIT:
            continue
        ra, rb = la.get(key), lb.get(key)
        entry = {"protocol_key": key, "a": _brief(ra), "b": _brief(rb)}
        problem = None
        if ra is None or rb is None:
            problem = "Run this protocol on both versions before comparing."
        elif ra["status"] not in ("completed", "completed_with_warnings") or rb["status"] not in ("completed", "completed_with_warnings"):
            problem = "Both versions need a completed run of this protocol. A failed or insufficient run is not compared."
        elif ra["protocol_hash"] != rb["protocol_hash"] or ra["engine_version"] != rb["engine_version"]:
            problem = "The two runs used different protocol definitions, so they are not compared."
        elif min(ra["n_scored"] or 0, rb["n_scored"] or 0) < MIN_COMPARE_SCORED:
            problem = f"Fewer than {MIN_COMPARE_SCORED} held out rows were scored in one of the runs."
        if problem:
            entry.update({"comparable": False, "reason": problem})
        else:
            same_rows = ra["evaluation_rows_hash"] == rb["evaluation_rows_hash"]
            ma, mb = ra["result"]["metrics"], rb["result"]["metrics"]
            ca, cb = ra["result"]["calibration"], rb["result"]["calibration"]
            entry.update({
                "comparable": True, "same_evaluation_rows": same_rows,
                "reading": ("Same protocol on the same held out rows. Differences reflect the models." if same_rows else
                            "Same protocol, but the two versions were evaluated on different rows. Differences may reflect the "
                            "evidence as much as the model. Treat them as indicative."),
                "metrics": {m: {"a": ma[m], "b": mb[m], "difference": (mb[m] - ma[m]) if ma[m] is not None and mb[m] is not None else None}
                            for m in ("mae", "rmse", "r2", "skill_vs_mean_baseline")},
                "sharpness": {"a": ca["sharpness"]["mean_sigma"], "b": cb["sharpness"]["mean_sigma"]},
                "mean_crps": {"a": ca["mean_crps"], "b": cb["mean_crps"]},
                "calibration_reading": {"a": ca["assessment"], "b": cb["assessment"]},
                "n_scored": {"a": ra["n_scored"], "b": rb["n_scored"]},
            })
        rows.append(entry)
    return {"a": summarise_version(organization_id, a), "b": summarise_version(organization_id, b), "protocols": rows,
            "note": "No version is declared better. Differences are shown with their sample counts. No significance test is applied, "
                    "and small differences between small samples carry no weight."}
