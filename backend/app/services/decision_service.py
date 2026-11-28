"""Advanced substitution decision engine: setup, customer entered values, and immutable analyses.

The system never supplies a cost, a supplier, an environmental figure or any other business value. It uses only what the
customer entered (with the source they gave) and what the model predicted, and everything else is unknown. Unknown is
never read as passing. A rank, a position on the Pareto front or a prediction is never described as qualification."""
import json
import math

from backend.app.repositories import candidate_repo, change_case_repo, decision_repo as repo, model_registry_repo, program_workspace_repo as workspace_repo
from backend.app.schemas.errors import Forbidden, ValidationError
from backend.app.services import change_case_service
from backend.app.services import program_workspace_rules as workspace_rules
from engine.decision import DECISION_ENGINE_VERSION, QUALIFICATION_STATEMENT
from engine.decision import analysis as engine_analysis
from engine.decision import constraints as engine_constraints
from engine.validation.identity import sha256_json

MANAGER_MESSAGE = "Only an owner, an admin or the program owner can change the decision setup."
RUN_MESSAGE = "Only an owner, an admin, the program owner or a reviewer can run a decision analysis."
MAX_ATTRIBUTES, MAX_CONSTRAINTS, MAX_OBJECTIVES, MAX_CANDIDATES = 50, 100, 6, 500

CONSTRAINT_CATEGORIES = {
    "property_threshold": "Property threshold", "composition_range": "Composition range", "process_constraint": "Process constraint",
    "manufacturing_restriction": "Manufacturing restriction", "material_exclusion": "Material exclusion", "cost_limit": "Cost limit",
    "supplier_restriction": "Supplier restriction", "customer_rule": "Customer specific rule",
}
ATTRIBUTE_CATEGORIES = {
    "cost": "Cost", "supplier": "Supplier", "process": "Process", "manufacturing": "Manufacturing", "environmental": "Environmental",
    "supply": "Supply reliability", "composition": "Composition", "customer": "Customer specific", "other": "Other",
}
OPERATOR_LABELS = engine_constraints.OPERATOR_LABEL
DOMAIN_LABEL = {"within_historical_domain": "Within observed range", "near_edge_of_domain": "Near the edge of the observed range",
                "outside_historical_domain": "Outside the observed range"}
CLASS_LABEL = {"within_training_spacing": "Within training spacing", "sparse_region": "Sparse region", "isolated": "Isolated", "not_assessed": "Not assessed"}
VALIDATION_LABEL = {"completed": "Validation completed", "completed_with_warnings": "Validation completed with warnings",
                    "incomplete": "Validation incomplete", "failed": "Validation failed"}


# ------------------------------------------------------------------ access
def _case(org, case_id):
    case = change_case_repo.get_change_case(org, case_id)
    if case is None:
        raise LookupError("Program not found.")
    return case


def _writable(case):
    from backend.app.services import change_case_lifecycle_rules as lifecycle_rules
    lifecycle_rules.check_not_archived(case)
    if case["status"] == "completed":
        raise ValidationError("This program is closed and can no longer be changed.", field="status")


def _is_manager(role, user_id, case):
    return role in workspace_rules.DECISION_ROLES or case.get("owner_user_id") == user_id


def _require_manager(org, role, user_id, case):
    if not _is_manager(role, user_id, case):
        raise Forbidden(MANAGER_MESSAGE)


def _may_run(org, role, user_id, case):
    return _is_manager(role, user_id, case) or workspace_repo.is_reviewer(org, case["id"], user_id)


# ------------------------------------------------------------------ references
def references(case: dict, attributes: list) -> dict:
    """Everything a constraint or objective may point at, with its type and unit."""
    spec = json.loads(case["qualification_spec_json"])
    units = spec.get("units") or {}
    target = spec.get("target_metric")
    refs = {}
    for name in spec.get("feature_columns") or []:
        refs[f"feature:{name}"] = {"ref": f"feature:{name}", "label": f"{name} (model input)", "value_type": "number", "unit": units.get(name),
                                   "origin": "model_input", "group": "Composition and model inputs"}
    refs["predicted:target"] = {"ref": "predicted:target", "label": f"{target} (model prediction)", "value_type": "number", "unit": units.get(target),
                                "origin": "prediction", "group": "Predicted property"}
    for a in attributes:
        if a["status"] == "active":
            refs[f"attr:{a['attr_key']}"] = {"ref": f"attr:{a['attr_key']}", "label": a["label"], "value_type": a["value_type"], "unit": a["unit"],
                                              "origin": "customer", "group": ATTRIBUTE_CATEGORIES.get(a["category"], "Other")}
    return refs


def _check_value(value_type: str, raw, field="value"):
    if value_type == "number":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise ValidationError("Enter a finite number.", field=field)
        return float(raw)
    if value_type == "boolean":
        if not isinstance(raw, bool):
            raise ValidationError("Choose yes or no.", field=field)
        return raw
    if value_type == "text":
        if not isinstance(raw, str) or not raw.strip() or len(raw) > 200:
            raise ValidationError("Enter text of up to 200 characters.", field=field)
        return raw.strip()
    if not isinstance(raw, list) or not raw or len(raw) > 30 or any(not isinstance(x, str) or not x.strip() or len(x) > 60 for x in raw):
        raise ValidationError("Enter a list of up to 30 short items.", field=field)
    return [x.strip() for x in raw]


def _check_constraint_fields(case, attributes, f: dict) -> dict:
    refs = references(case, attributes)
    ref = refs.get(f["ref"])
    if ref is None:
        raise ValidationError("Choose a property, input or attribute from the list.", field="ref")
    if f["category"] not in CONSTRAINT_CATEGORIES:
        raise ValidationError("Choose a listed category.", field="category")
    op, vt = f["operator"], ref["value_type"]
    if op not in engine_constraints.OPERATORS_BY_TYPE[vt]:
        raise ValidationError(f"That operator does not apply to a {vt.replace('_', ' ')} value.", field="operator")
    if ref["origin"] == "prediction" and op == "==":
        raise ValidationError("An exact match cannot be shown for a predicted value. Use a limit or a range.", field="operator")
    value = f["value"]
    if vt == "number":
        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ValidationError("Enter a lower and an upper limit.", field="value")
            lo, hi = _check_value("number", value[0]), _check_value("number", value[1])
            if not lo < hi:
                raise ValidationError("The lower limit must be below the upper limit.", field="value")
            value = [lo, hi]
        else:
            value = _check_value("number", value)
    elif vt == "text":
        value = _check_value("text_list", value) if op in ("in", "not_in") else _check_value("text", value)
    elif vt == "boolean":
        value = _check_value("boolean", value)
    else:
        value = _check_value("text_list", value)
    if f.get("unit") not in (None, "", ref["unit"]):
        raise ValidationError(f"Use the unit of {ref['label']}: {ref['unit'] or 'none'}. Units are never converted silently.", field="unit")
    return {**f, "value": value, "unit": ref["unit"]}


# ------------------------------------------------------------------ setup views
def _attribute_view(a, counts):
    return {"id": a["id"], "key": a["attr_key"], "label": a["label"], "value_type": a["value_type"], "unit": a["unit"], "category": a["category"],
            "description": a["description"], "status": a["status"], "values_recorded": counts.get(a["id"], 0)}


def _constraint_view(c, refs):
    ref = refs.get(c["ref"])
    return {"id": c["id"], "name": c["name"], "kind": c["kind"], "category": c["category"], "ref": c["ref"], "ref_label": ref["label"] if ref else c["ref"],
            "ref_available": ref is not None, "operator": c["operator"], "operator_label": OPERATOR_LABELS.get(c["operator"], c["operator"]), "value": c["value"],
            "unit": c["unit"], "rationale": c["rationale"], "status": c["status"]}


def _objective_view(o, refs):
    ref = refs.get(o["ref"])
    return {"id": o["id"], "name": o["name"], "ref": o["ref"], "ref_label": ref["label"] if ref else o["ref"], "ref_available": ref is not None,
            "direction": o["direction"], "unit": o["unit"], "status": o["status"]}


def workspace(org: int, role: str, user_id: int, case_id: int) -> dict:
    case = _case(org, case_id)
    attributes = repo.list_attributes(org, case_id)
    refs = references(case, attributes)
    values = repo.list_values(org, case_id)
    counts = {}
    for v in values:
        counts[v["attribute_id"]] = counts.get(v["attribute_id"], 0) + 1
    candidates = _active_candidates(org, case_id)
    value_map = {(v["candidate_id"], v["attribute_id"]): v for v in values}
    latest = repo.latest_analysis_id(org, case_id)
    outdated = None
    if latest is not None:
        outdated = repo.get_analysis(org, case_id, latest)["input_hash"] != build_snapshot(org, case, candidates)[1]
    return {
        "attributes": [_attribute_view(a, counts) for a in attributes],
        "constraints": [_constraint_view(c, refs) for c in repo.list_constraints(org, case_id)],
        "objectives": [_objective_view(o, refs) for o in repo.list_objectives(org, case_id)],
        "references": list(refs.values()),
        "operators": {t: [{"value": o, "label": OPERATOR_LABELS[o]} for o in ops] for t, ops in engine_constraints.OPERATORS_BY_TYPE.items()},
        "constraint_categories": [{"value": k, "label": v} for k, v in CONSTRAINT_CATEGORIES.items()],
        "attribute_categories": [{"value": k, "label": v} for k, v in ATTRIBUTE_CATEGORIES.items()],
        "candidate_values": [{"candidate_id": c["id"], "name": c["candidate_name"], "values": {
            str(a["id"]): ({"value": value_map[(c["id"], a["id"])]["value"], "source_note": value_map[(c["id"], a["id"])]["source_note"],
                            "entered_at": value_map[(c["id"], a["id"])]["entered_at"]} if (c["id"], a["id"]) in value_map else None)
            for a in attributes}} for c in candidates],
        "latest_analysis_id": latest, "analysis_outdated": outdated,
        "analyses": repo.list_analyses(org, case_id),
        "permissions": {"can_manage": _is_manager(role, user_id, case), "can_run": _may_run(org, role, user_id, case), "can_enter_values": True},
        "no_invented_data": "Costs, suppliers, environmental figures and process facts come only from what your team enters here. "
                            "Anything not entered is shown as unknown.",
    }


# ------------------------------------------------------------------ attributes and values
def create_attribute(org, role, user_id, case_id, body) -> dict:
    case = _case(org, case_id)
    _require_manager(org, role, user_id, case)
    _writable(case)
    if body.category not in ATTRIBUTE_CATEGORIES:
        raise ValidationError("Choose a listed category.", field="category")
    existing = repo.list_attributes(org, case_id)
    if len(existing) >= MAX_ATTRIBUTES:
        raise ValidationError(f"A program can track up to {MAX_ATTRIBUTES} attributes.", field="key")
    spec = json.loads(case["qualification_spec_json"])
    if any(a["attr_key"] == body.key for a in existing):
        raise ValidationError("An attribute with this key already exists.", field="key")
    if body.value_type != "number" and body.unit:
        raise ValidationError("Only number attributes have a unit.", field="unit")
    attribute = repo.create_attribute(org, case_id, body.key, body.label.strip(), body.value_type, (body.unit or None), body.category,
                                      (body.description or None), user_id)
    return _attribute_view(attribute, {})


def update_attribute(org, role, user_id, case_id, attribute_id, body) -> dict:
    case = _case(org, case_id)
    _require_manager(org, role, user_id, case)
    _writable(case)
    attribute = repo.get_attribute(org, case_id, attribute_id)
    if attribute is None:
        raise LookupError("Attribute not found.")
    fields = body.model_dump(exclude_unset=True)
    if fields.get("category") and fields["category"] not in ATTRIBUTE_CATEGORIES:
        raise ValidationError("Choose a listed category.", field="category")
    if fields.get("status") == "archived":
        ref = f"attr:{attribute['attr_key']}"
        users = [c["name"] for c in repo.list_constraints(org, case_id, False) if c["ref"] == ref] + [o["name"] for o in repo.list_objectives(org, case_id, False) if o["ref"] == ref]
        if users:
            raise ValidationError("Archive or change these first: " + ", ".join(users) + ".", field="status")
    updated = repo.update_attribute(org, case_id, attribute_id, fields)
    return _attribute_view(updated, {attribute_id: repo.count_values_for_attribute(org, case_id, attribute_id)})


def set_value(org, role, user_id, case_id, candidate_id, attribute_id, body) -> dict:
    case = _case(org, case_id)
    _writable(case)
    attribute = repo.get_attribute(org, case_id, attribute_id)
    candidate = candidate_repo.get_candidate(org, candidate_id)
    if attribute is None or attribute["status"] != "active":
        raise LookupError("Attribute not found.")
    if candidate is None or candidate["change_case_id"] != case_id or candidate.get("archived_at"):
        raise LookupError("Candidate not found.")
    value = _check_value(attribute["value_type"], body.value)
    previous = repo.get_value(org, case_id, candidate_id, attribute_id)
    record = repo.set_value(org, case_id, candidate_id, attribute_id, value, body.source_note.strip(), user_id)
    return {"record": {"candidate_id": candidate_id, "attribute_id": attribute_id, "value": record["value"], "source_note": record["source_note"],
                       "entered_at": record["entered_at"]}, "previous": previous["value"] if previous else None}


def clear_value(org, role, user_id, case_id, candidate_id, attribute_id) -> dict:
    case = _case(org, case_id)
    _writable(case)
    attribute = repo.get_attribute(org, case_id, attribute_id)
    if attribute is None:
        raise LookupError("Attribute not found.")
    previous = repo.get_value(org, case_id, candidate_id, attribute_id)
    if previous is None:
        raise LookupError("No value is recorded.")
    repo.clear_value(org, case_id, candidate_id, attribute_id)
    return {"cleared": True, "previous": previous["value"]}


# ------------------------------------------------------------------ constraints and objectives
def create_constraint(org, role, user_id, case_id, body) -> dict:
    case = _case(org, case_id)
    _require_manager(org, role, user_id, case)
    _writable(case)
    if len(repo.list_constraints(org, case_id)) >= MAX_CONSTRAINTS:
        raise ValidationError(f"A program can have up to {MAX_CONSTRAINTS} constraints.", field="name")
    attributes = repo.list_attributes(org, case_id)
    fields = _check_constraint_fields(case, attributes, body.model_dump())
    created = repo.create_constraint(org, case_id, fields, user_id)
    return _constraint_view(created, references(case, attributes))


def update_constraint(org, role, user_id, case_id, constraint_id, body) -> dict:
    case = _case(org, case_id)
    _require_manager(org, role, user_id, case)
    _writable(case)
    current = repo.get_constraint(org, case_id, constraint_id)
    if current is None:
        raise LookupError("Constraint not found.")
    attributes = repo.list_attributes(org, case_id)
    changes = body.model_dump(exclude_unset=True)
    merged = {**{k: current[k] for k in ("name", "kind", "category", "ref", "operator", "value", "unit", "rationale")}, **{k: v for k, v in changes.items() if k != "status"}}
    if set(changes) - {"status", "name", "rationale", "kind"} or (changes.get("status") == "active" and current["status"] == "archived"):
        merged = _check_constraint_fields(case, attributes, merged)  # a changed rule or a restored one is checked again in full
    fields = {**merged, **({"status": changes["status"]} if "status" in changes else {})}
    updated = repo.update_constraint(org, case_id, constraint_id, fields)
    return _constraint_view(updated, references(case, attributes))


def create_objective(org, role, user_id, case_id, body) -> dict:
    case = _case(org, case_id)
    _require_manager(org, role, user_id, case)
    _writable(case)
    attributes = repo.list_attributes(org, case_id)
    refs = references(case, attributes)
    ref = refs.get(body.ref)
    if ref is None or ref["value_type"] != "number":
        raise ValidationError("An objective needs a number: a predicted property, a model input or a number attribute.", field="ref")
    active = repo.list_objectives(org, case_id, include_archived=False)
    if len(active) >= MAX_OBJECTIVES:
        raise ValidationError(f"Use at most {MAX_OBJECTIVES} objectives. More make a Pareto front too wide to read.", field="ref")
    if any(o["ref"] == body.ref for o in active):
        raise ValidationError("This quantity is already an objective.", field="ref")
    created = repo.create_objective(org, case_id, body.name.strip(), body.ref, body.direction, ref["unit"], user_id)
    return _objective_view(created, refs)


def update_objective(org, role, user_id, case_id, objective_id, body) -> dict:
    case = _case(org, case_id)
    _require_manager(org, role, user_id, case)
    _writable(case)
    current = repo.get_objective(org, case_id, objective_id)
    if current is None:
        raise LookupError("Objective not found.")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("status") == "active" and current["status"] == "archived":
        if any(o["ref"] == current["ref"] for o in repo.list_objectives(org, case_id, include_archived=False)):
            raise ValidationError("This quantity is already an active objective.", field="status")
        if len(repo.list_objectives(org, case_id, include_archived=False)) >= MAX_OBJECTIVES:
            raise ValidationError(f"Use at most {MAX_OBJECTIVES} objectives.", field="status")
    updated = repo.update_objective(org, case_id, objective_id, changes)
    return _objective_view(updated, references(case, repo.list_attributes(org, case_id)))


# ------------------------------------------------------------------ snapshot and analysis
def _active_candidates(org, case_id):
    return change_case_service.list_candidates_with_predictions(org, case_id, include_archived=False)


def _validation_label(org, version_id, cache):
    if version_id is None:
        return None
    if version_id not in cache:
        from backend.app.services import model_validation_service as mv
        cache[version_id] = VALIDATION_LABEL.get(mv._state(model_registry_repo.list_validations(org, version_id))["state"])
    return cache[version_id]


def build_snapshot(org: int, case: dict, candidates: list = None):
    """The full input of an analysis, and its hash. Nothing in it is computed or guessed: it is what was entered, what the
    model predicted, and the rules in force."""
    case_id = case["id"]
    candidates = candidates if candidates is not None else _active_candidates(org, case_id)
    if len(candidates) > MAX_CANDIDATES:
        raise ValidationError(f"A decision analysis covers up to {MAX_CANDIDATES} candidates.", field="candidates")
    spec = json.loads(case["qualification_spec_json"])
    units = spec.get("units") or {}
    target = {"name": spec.get("target_metric"), "unit": units.get(spec.get("target_metric")), "direction": spec.get("direction", "maximize")}
    attributes = [a for a in repo.list_attributes(org, case_id) if a["status"] == "active"]
    refs = references(case, attributes)
    constraints = [c for c in repo.list_constraints(org, case_id, include_archived=False) if c["ref"] in refs]
    objectives = [o for o in repo.list_objectives(org, case_id, include_archived=False) if o["ref"] in refs]
    attr_by_id = {a["id"]: a for a in attributes}
    values = {}
    for v in repo.list_values(org, case_id):
        if v["attribute_id"] in attr_by_id:
            values.setdefault(v["candidate_id"], {})[v["attribute_id"]] = v
    outcomes = repo.outcome_summary(org, case_id)
    validation_cache = {}
    cand_snap = []
    for c in sorted(candidates, key=lambda x: x["id"]):
        facts, unavailable = {}, {}
        for name in spec.get("feature_columns") or []:
            if name in c["properties"]:
                facts[f"feature:{name}"] = {"value": c["properties"][name], "sd": None, "unit": units.get(name), "origin": "model_input"}
        pred = c.get("latest_prediction")
        if pred is None:
            unavailable["predicted:target"] = "No prediction exists yet. Rank the candidates first."
        elif c.get("prediction_inputs_stale"):
            unavailable["predicted:target"] = "The prediction is out of date because this candidate's inputs changed. Rank again."
        elif c.get("prediction_stale"):
            unavailable["predicted:target"] = "The prediction is out of date because newer evidence exists. Rank again."
        elif pred.get("predicted_value") is None:
            unavailable["predicted:target"] = "No predicted value is recorded for this candidate."
        else:
            facts["predicted:target"] = {"value": pred["predicted_value"], "sd": pred.get("uncertainty_std"), "unit": units.get(spec.get("target_metric")),
                                         "origin": "prediction", "prediction_id": pred["id"], "model_version_id": pred.get("model_version_id")}
        for attribute_id, v in values.get(c["id"], {}).items():
            a = attr_by_id[attribute_id]
            facts[f"attr:{a['attr_key']}"] = {"value": v["value"], "sd": None, "unit": a["unit"], "origin": "customer", "source_note": v["source_note"]}
        space = c.get("input_space_applicability") or {}
        ds = c.get("decision_support") or {}
        dom = c.get("domain_coverage") or {}
        cand_snap.append({
            "id": c["id"], "name": c["candidate_name"], "facts": facts, "unavailable": unavailable,
            "context": {"domain_label": DOMAIN_LABEL.get(dom.get("status")), "input_space_class": space.get("class"),
                        "input_space_label": CLASS_LABEL.get(space.get("class")) if space else None, "decision_support_label": ds.get("label"),
                        "model_version_number": space.get("model_version_number"), "model_version_id": space.get("model_version_id"),
                        "validation_state": _validation_label(org, space.get("model_version_id"), validation_cache),
                        "stale": bool(c.get("prediction_stale") or c.get("prediction_inputs_stale")), "physical": outcomes.get(c["id"], {"passed": 0, "failed": 0})}})
    snapshot = {
        "engine_version": DECISION_ENGINE_VERSION, "program": {"id": case_id, "name": case["name"]}, "target": target,
        "constraints": [{"id": c["id"], "name": c["name"], "kind": c["kind"], "category": c["category"], "ref": c["ref"], "operator": c["operator"],
                         "value": c["value"], "unit": c["unit"], "rationale": c["rationale"]} for c in constraints],
        "objectives": [{"id": o["id"], "name": o["name"], "ref": o["ref"], "direction": o["direction"], "unit": o["unit"]} for o in objectives],
        "attributes": [{"key": a["attr_key"], "label": a["label"], "value_type": a["value_type"], "unit": a["unit"], "category": a["category"]} for a in attributes],
        "candidates": cand_snap,
    }
    return snapshot, sha256_json(snapshot)


def run_analysis(org: int, role: str, user_id: int, case_id: int) -> dict:
    case = _case(org, case_id)
    if not _may_run(org, role, user_id, case):
        raise Forbidden(RUN_MESSAGE)
    change_case_service._check_entitled(org)
    _writable(case)
    snapshot, digest = build_snapshot(org, case)
    if not snapshot["candidates"]:
        raise ValidationError("Add at least one candidate before running a decision analysis.", field="candidates")
    result = engine_analysis.analyse(snapshot["candidates"], snapshot["constraints"], snapshot["objectives"])
    facts_by_id = {c["id"]: c for c in snapshot["candidates"]}
    for key, row in result["candidates"].items():
        f = facts_by_id[row["candidate_id"]]
        pred = f["facts"].get("predicted:target")
        if pred:
            lo, hi = engine_constraints.interval(pred)
            row["predicted"] = {"available": True, "value": pred["value"], "sd": pred["sd"], "interval": [lo, hi] if lo != hi else None, "unit": pred["unit"]}
        else:
            row["predicted"] = {"available": False, "reason": f["unavailable"].get("predicted:target", "No prediction.")}
        row["qualification_status"] = "not_qualified"
        row["physical_validation"] = _physical_label(f["context"]["physical"])
    summary = {**{k: result["summary"][k] for k in ("counts", "candidates", "hard_constraints", "soft_constraints", "front", "provisional_front")},
               "objectives": result["summary"]["objectives"]}
    record = repo.create_analysis(org, case_id, digest, DECISION_ENGINE_VERSION, snapshot, result, summary, user_id)
    return {"analysis": analysis_view(record)}


def _physical_label(p):
    if p["passed"] or p["failed"]:
        return {"status": "outcomes_recorded", "passed": p["passed"], "failed": p["failed"],
                "text": f"{p['passed']} outcome{'s' if p['passed'] != 1 else ''} met the specification, {p['failed']} did not. Not a qualification."}
    return {"status": "none_recorded", "passed": 0, "failed": 0, "text": "No physical outcome is recorded."}


def analysis_view(record: dict) -> dict:
    return {"id": record["id"], "created_at": record["created_at"], "engine_version": record["engine_version"], "input_hash": record["input_hash"],
            "program": record["snapshot"]["program"], "target": record["snapshot"]["target"], "constraints": record["snapshot"]["constraints"],
            "objectives": record["snapshot"]["objectives"], "summary": record["result"]["summary"], "order": record["result"]["order"],
            "candidates": record["result"]["candidates"], "qualification": QUALIFICATION_STATEMENT}


def get_analysis(org, case_id, analysis_id) -> dict:
    _case(org, case_id)
    record = repo.get_analysis(org, case_id, analysis_id)
    if record is None:
        raise LookupError("Analysis not found.")
    return analysis_view(record)
