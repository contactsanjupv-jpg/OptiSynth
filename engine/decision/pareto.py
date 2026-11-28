"""Pareto analysis without weights.

Candidate A dominates B when A is at least as good on every objective and strictly better on at least one, judged on
point values. Dominance is also checked against the model interval: A dominates B CONFIDENTLY when A's worst case is at
least as good as B's best case on every objective and strictly better on one. Candidates dominated only on point values
are shown as such. No objective is traded against another, so nothing is scaled or summed."""


def _sign(direction):
    return 1.0 if direction == "minimize" else -1.0


def _bounds(entry, direction):
    """(point, best, worst) in a 'smaller is better' frame."""
    s = _sign(direction)
    lo, hi = entry["interval"] if entry.get("interval") else (entry["value"], entry["value"])
    a, b = s * lo, s * hi
    return s * entry["value"], min(a, b), max(a, b)


def dominates(a: list, b: list) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def confidently_dominates(a: list, b: list) -> bool:
    """a and b are lists of (point, best, worst). a's worst must not exceed b's best on any objective."""
    return all(x[2] <= y[1] for x, y in zip(a, b)) and any(x[2] < y[1] for x, y in zip(a, b))


def analyse(members: dict, objectives: list) -> dict:
    """members: {id: {objective_ref: {"value", "interval"}}} for candidates with a value on EVERY objective.
    Returns {id: {"layer", "dominated_by", "confidently_dominated_by"}}."""
    directions = {o["ref"]: o["direction"] for o in objectives}
    refs = [o["ref"] for o in objectives]
    frame = {i: [_bounds(m[r], directions[r]) for r in refs] for i, m in members.items()}
    points = {i: [t[0] for t in v] for i, v in frame.items()}
    ids = sorted(members)
    out = {i: {"layer": None, "dominated_by": [], "confidently_dominated_by": []} for i in ids}
    for a in ids:
        for b in ids:
            if a != b and dominates(points[a], points[b]):
                out[b]["dominated_by"].append(a)
                if confidently_dominates(frame[a], frame[b]):
                    out[b]["confidently_dominated_by"].append(a)
    remaining, layer = set(ids), 1
    while remaining:
        front = sorted(i for i in remaining if not any(d in remaining for d in out[i]["dominated_by"]))
        for i in front:
            out[i]["layer"] = layer
        remaining -= set(front)
        layer += 1
    return out
