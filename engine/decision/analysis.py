"""Orchestrates one decision analysis: evaluates constraints, groups candidates, runs the Pareto analysis, measures what
each constraint contributes, and builds plain language explanations from the structured facts."""
import math

from engine.decision import QUALIFICATION_STATEMENT, INTERVAL_NOTE
from engine.decision import constraints as C
from engine.decision import pareto

FEASIBLE, UNRESOLVED, REJECTED = "feasible", "unresolved", "rejected"
GROUP_ORDER = {FEASIBLE: 0, UNRESOLVED: 1, REJECTED: 2}
FRONT, DOMINATED, NOT_COMPARABLE, NOT_ELIGIBLE, NO_OBJECTIVES = "front", "dominated", "not_comparable", "not_eligible", "no_objectives"
NEAR_MISS_FRACTION = 0.10  # working convention for calling a miss "near", shown with every statement that uses it


def _group(hard_states: list) -> str:
    if C.VIOLATED in hard_states:
        return REJECTED
    if C.UNKNOWN in hard_states:
        return UNRESOLVED
    return FEASIBLE


def _evaluate_all(candidate, constraints):
    facts, unavailable = candidate["facts"], candidate.get("unavailable", {})
    out = []
    for con in constraints:
        res = C.evaluate(con, facts.get(con["ref"]), unavailable.get(con["ref"]))
        out.append({"constraint_id": con["id"], "name": con["name"], "kind": con["kind"], "category": con["category"], "ref": con["ref"],
                    "operator": con["operator"], "limit": con["value"], "unit": con.get("unit"), **res})
    return out


def _objective_entry(candidate, obj):
    fact = candidate["facts"].get(obj["ref"])
    if fact is None or not isinstance(fact["value"], (int, float)) or isinstance(fact["value"], bool) or not math.isfinite(fact["value"]):
        return None
    lo, hi = C.interval(fact)
    return {"value": float(fact["value"]), "sd": fact.get("sd"), "interval": [lo, hi] if lo != hi else None}


def analyse(candidates: list, constraints: list, objectives: list) -> dict:
    candidates = sorted(candidates, key=lambda c: c["id"])
    hard_cons = [c for c in constraints if c["kind"] == "hard"]
    rows = {}
    for cand in candidates:
        evals = _evaluate_all(cand, constraints)
        hard = [e for e in evals if e["kind"] == "hard"]
        soft = [e for e in evals if e["kind"] == "soft"]
        entries = {o["ref"]: _objective_entry(cand, o) for o in objectives}
        rows[cand["id"]] = {
            "candidate_id": cand["id"], "name": cand["name"], "group": _group([h["state"] for h in hard]),
            "hard": hard, "soft": soft, "objective_values": entries, "context": cand.get("context", {}),
            "violated_hard": [h["constraint_id"] for h in hard if h["state"] == C.VIOLATED],
            "unknown_hard": [h["constraint_id"] for h in hard if h["state"] == C.UNKNOWN],
            "soft_summary": {"met": sum(s["state"] == C.SATISFIED for s in soft), "missed": sum(s["state"] == C.VIOLATED for s in soft),
                             "unknown": sum(s["state"] == C.UNKNOWN for s in soft), "total": len(soft)},
            "pareto": {"status": NOT_ELIGIBLE, "layer": None, "dominated_by": [], "confidently_dominated_by": [], "missing": []},
            "provisional": {"layer": None, "dominated_by": [], "confidently_dominated_by": []},
        }
    obj_by_ref = {o["ref"]: o for o in objectives}

    def run_pareto(ids):
        comparable = {i: {r: rows[i]["objective_values"][r] for r in obj_by_ref} for i in ids
                      if all(rows[i]["objective_values"][r] is not None for r in obj_by_ref)}
        return pareto.analyse(comparable, objectives) if comparable and objectives else {}, comparable

    feasible_ids = [i for i, r in rows.items() if r["group"] == FEASIBLE]
    result, comparable = run_pareto(feasible_ids)
    for i in feasible_ids:
        p = rows[i]["pareto"]
        if not objectives:
            p["status"] = NO_OBJECTIVES
        elif i not in comparable:
            p["status"] = NOT_COMPARABLE
            p["missing"] = [o["name"] for o in objectives if rows[i]["objective_values"][o["ref"]] is None]
        else:
            r = result[i]
            p.update(status=FRONT if r["layer"] == 1 else DOMINATED, layer=r["layer"], dominated_by=r["dominated_by"],
                     confidently_dominated_by=r["confidently_dominated_by"])
    provisional_ids = [i for i, r in rows.items() if r["group"] in (FEASIBLE, UNRESOLVED)]
    pres, _ = run_pareto(provisional_ids)
    for i, r in pres.items():
        rows[i]["provisional"] = {"layer": r["layer"], "dominated_by": r["dominated_by"], "confidently_dominated_by": r["confidently_dominated_by"]}

    # what each hard constraint contributes: regroup without it
    impact = []
    for con in hard_cons:
        sole, changes, violated, unknown = [], {}, 0, 0
        for i, r in rows.items():
            states = {h["constraint_id"]: h["state"] for h in r["hard"]}
            violated += states[con["id"]] == C.VIOLATED
            unknown += states[con["id"]] == C.UNKNOWN
            without = _group([s for k, s in states.items() if k != con["id"]])
            if without != r["group"]:
                changes[i] = without
            if states[con["id"]] == C.VIOLATED and r["violated_hard"] == [con["id"]]:
                sole.append(i)
        impact.append({"constraint_id": con["id"], "name": con["name"], "violated_by": violated, "unknown_for": unknown,
                       "sole_blocker_for": sole, "would_change_if_removed": {str(k): v for k, v in sorted(changes.items())}})
    impact.sort(key=lambda x: (-len(x["would_change_if_removed"]), -x["violated_by"], x["constraint_id"]))

    # missing information, from what the constraints and objectives actually needed
    for i, r in rows.items():
        missing = []
        for e in r["hard"] + r["soft"]:
            if e["state"] == C.UNKNOWN:
                missing.append({"ref": e["ref"], "needed_for": e["name"], "kind": e["kind"], "reason": e["detail"]})
        for o in objectives:
            if r["objective_values"][o["ref"]] is None:
                missing.append({"ref": o["ref"], "needed_for": o["name"], "kind": "objective", "reason": "No value is recorded."})
        r["missing_information"] = missing

    summary = {
        "counts": {g: sum(r["group"] == g for r in rows.values()) for g in (FEASIBLE, UNRESOLVED, REJECTED)},
        "candidates": len(rows), "hard_constraints": len(hard_cons), "soft_constraints": len(constraints) - len(hard_cons),
        "objectives": [{"ref": o["ref"], "name": o["name"], "direction": o["direction"]} for o in objectives],
        "front": sorted(i for i, r in rows.items() if r["pareto"]["status"] == FRONT),
        "provisional_front": sorted(i for i, r in rows.items() if r["provisional"]["layer"] == 1),
        "not_confidently_dominated": sorted(i for i, r in rows.items() if r["pareto"]["status"] in (FRONT, DOMINATED)
                                            and not r["pareto"]["confidently_dominated_by"]),
        "constraint_impact": impact, "interval_note": INTERVAL_NOTE, "qualification": QUALIFICATION_STATEMENT,
        "method": ("Candidates are grouped by hard constraints, then compared on the objectives by Pareto dominance. No weights "
                   "are used and no objective is traded against another. Values that were not recorded stay unknown."),
    }
    names = {i: r["name"] for i, r in rows.items()}
    cons_by_id = {c["id"]: c for c in constraints}
    for r in rows.values():
        r["explanation"] = explain(r, summary, names, cons_by_id, obj_by_ref)
    order = sorted(rows, key=lambda i: (GROUP_ORDER[rows[i]["group"]], rows[i]["pareto"]["layer"] or 99, rows[i]["name"].lower(), i))
    return {"summary": summary, "order": order, "candidates": {str(i): rows[i] for i in rows}}


def _num(x):
    return f"{x:.6g}"


def explain(r, summary, names, cons_by_id, objectives) -> dict:
    p, ctx = r["pareto"], r["context"]
    violated = [h for h in r["hard"] if h["state"] == C.VIOLATED]
    unknown = [h for h in r["hard"] if h["state"] == C.UNKNOWN]
    impact = {x["constraint_id"]: x for x in summary["constraint_impact"]}
    promising, rejected, dominating, supported, could = [], [], [], [], []

    if r["group"] == REJECTED:
        headline = f"Rejected. It violates {len(violated)} hard constraint{'s' if len(violated) != 1 else ''}."
    elif r["group"] == UNRESOLVED:
        headline = (f"Not confirmed feasible. {len(unknown)} hard constraint{'s' if len(unknown) != 1 else ''} cannot be checked "
                    "with the values on record. It is not treated as passing.")
    elif p["status"] == FRONT:
        headline = "Feasible and on the Pareto front. No other feasible candidate is at least as good on every objective."
    elif p["status"] == DOMINATED:
        by = ", ".join(names[i] for i in p["dominated_by"])
        conf = bool(p["confidently_dominated_by"])
        headline = (f"Feasible but dominated by {by}." if conf else
                    f"Feasible but dominated by {by} on point estimates only. The model intervals overlap, so the difference is not firm.")
    elif p["status"] == NOT_COMPARABLE:
        headline = f"Feasible, but not compared. No value is recorded for {', '.join(p['missing'])}."
    else:
        headline = "Feasible. No objectives are defined, so no trade off analysis was possible."

    if r["group"] == FEASIBLE:
        promising.append(f"Meets all {len(r['hard'])} hard constraint{'s' if len(r['hard']) != 1 else ''}." if r["hard"] else "No hard constraints are defined.")
        if p["status"] == FRONT and summary["objectives"]:
            promising.append("Not dominated on " + ", ".join(o["name"] for o in summary["objectives"]) + ".")
        if r["soft_summary"]["total"]:
            s = r["soft_summary"]
            promising.append(f"Soft constraints: {s['met']} met, {s['missed']} missed, {s['unknown']} unknown.")
    for h in violated:
        line = f"{h['name']}: {h['detail']}"
        if h["margin"] is not None:
            short = -h["margin"]
            lim = h["limit"] if isinstance(h["limit"], (int, float)) and not isinstance(h["limit"], bool) else None
            if lim:
                pct = short / abs(lim)
                line += f" Short by {_num(short)}{' ' + h['unit'] if h['unit'] else ''}, {pct * 100:.1f} percent of the limit."
                if pct <= NEAR_MISS_FRACTION:
                    line += f" That is a near miss (within {int(NEAR_MISS_FRACTION * 100)} percent, a working convention)."
        rejected.append(line)
        imp = impact.get(h["constraint_id"], {})
        if r["candidate_id"] in imp.get("sole_blocker_for", []):
            new = imp["would_change_if_removed"].get(str(r["candidate_id"]))
            dominating.append({"constraint_id": h["constraint_id"], "name": h["name"],
                               "effect": f"This is the only hard constraint it violates. Without it the candidate would be {new}."})
        else:
            dominating.append({"constraint_id": h["constraint_id"], "name": h["name"],
                               "effect": f"One of {len(violated)} hard constraints it violates. {imp.get('violated_by', 0)} candidates violate it."})
    for h in unknown:
        could.append(f"Record what is needed to check {h['name']}: {h['detail']}")
    for e in r["soft"]:
        if e["state"] == C.UNKNOWN:
            could.append(f"Soft constraint {e['name']} cannot be checked: {e['detail']}")
    if r["group"] == UNRESOLVED and r["provisional"]["layer"] == 1:
        could.append("If the missing values show it meets every hard constraint, it would be on the Pareto front with the current objectives.")
    if r["group"] == REJECTED and violated and all(impact.get(h["constraint_id"], {}).get("sole_blocker_for") for h in violated[:1]) and len(violated) == 1:
        could.append("Relaxing that one constraint, or new evidence that changes the value, would change the group.")
    if ctx.get("stale"):
        could.append("The prediction is out of date because inputs or evidence changed. Rank again to refresh it.")
    if ctx.get("input_space_class") in ("sparse_region", "isolated"):
        could.append("The model has little nearby evidence for this combination of inputs. A physical test would remove the dependence on the estimate.")
    for e in r["soft"]:
        if e["state"] == C.VIOLATED:
            rejected_soft = f"Soft trade off: {e['name']} is missed. {e['detail']}"
            could.append(rejected_soft)

    if ctx.get("model_version_number") is not None:
        supported.append(f"Prediction from model version {ctx['model_version_number']}.")
    for key, label in (("domain_label", "Per feature range check"), ("input_space_label", "Combination of inputs"), ("decision_support_label", "Decision support label"),
                       ("validation_state", "Model validation")):
        if ctx.get(key):
            supported.append(f"{label}: {ctx[key]}.")
    for h in r["hard"] + r["soft"]:
        if h["interval"]:
            supported.append(f"{h['name']} uses a predicted value with a model interval. {INTERVAL_NOTE}")
            break

    phys = ctx.get("physical") or {}
    qual = QUALIFICATION_STATEMENT
    if phys.get("passed") or phys.get("failed"):
        qual += f" Physical outcomes on record: {phys.get('passed', 0)} met the specification, {phys.get('failed', 0)} did not. This does not by itself qualify the material."
    else:
        qual += " No physical outcome is recorded for this candidate."
    return {"headline": headline, "promising": promising, "rejected": rejected, "dominating_constraints": dominating,
            "supported_by": supported, "could_change": could, "qualification": qual}
