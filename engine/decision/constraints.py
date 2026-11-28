"""Evaluate one constraint for one candidate. Three states only: satisfied, violated, unknown."""
import math

from engine.decision import Z95

SATISFIED, VIOLATED, UNKNOWN = "satisfied", "violated", "unknown"
NUMERIC_OPERATORS = (">=", ">", "<=", "<", "between", "==")
TEXT_OPERATORS = ("equals", "not_equals", "in", "not_in")
BOOLEAN_OPERATORS = ("equals",)
LIST_OPERATORS = ("excludes_any", "includes_any")
OPERATORS_BY_TYPE = {"number": NUMERIC_OPERATORS, "text": TEXT_OPERATORS, "boolean": BOOLEAN_OPERATORS, "text_list": LIST_OPERATORS}
OPERATOR_LABEL = {">=": "at least", ">": "above", "<=": "at most", "<": "below", "between": "between", "==": "equal to",
                  "equals": "equal to", "not_equals": "not equal to", "in": "one of", "not_in": "none of",
                  "excludes_any": "contains none of", "includes_any": "contains at least one of"}


def interval(fact: dict):
    value = float(fact["value"])
    sd = fact.get("sd")
    if sd is None or sd <= 0:
        return value, value
    return value - Z95 * sd, value + Z95 * sd


def _result(state, detail, observed=None, margin=None, ival=None, point_met=None):
    return {"state": state, "detail": detail, "observed": observed, "margin": margin, "interval": ival, "point_estimate_met": point_met}


def _fmt(x):
    return f"{x:.6g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else str(x)


def evaluate(constraint: dict, fact, reason: str = None) -> dict:
    """fact: {"value", "sd", ...} or None when nothing is recorded (then reason says why)."""
    op, target = constraint["operator"], constraint["value"]
    if fact is None:
        return _result(UNKNOWN, reason or "No value is recorded.")
    value = fact["value"]
    try:
        if op in NUMERIC_OPERATORS:
            return _numeric(op, target, fact)
        if op in ("equals", "not_equals"):
            same = _same(value, target)
            ok = same if op == "equals" else not same
            return _result(SATISFIED if ok else VIOLATED, f"Recorded value {_fmt(value)}.", observed=value)
        if op in ("in", "not_in"):
            inside = _norm(value) in {_norm(t) for t in target}
            ok = inside if op == "in" else not inside
            return _result(SATISFIED if ok else VIOLATED, f"Recorded value {_fmt(value)}.", observed=value)
        if op in ("excludes_any", "includes_any"):
            shared = sorted({_norm(v) for v in value} & {_norm(t) for t in target})
            ok = (not shared) if op == "excludes_any" else bool(shared)
            return _result(SATISFIED if ok else VIOLATED, ("Shares " + ", ".join(shared) + ".") if shared else "Shares none.", observed=value)
    except (TypeError, ValueError):
        return _result(UNKNOWN, "The recorded value does not match the type this constraint needs.")
    return _result(UNKNOWN, "This operator is not supported.")


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def _same(a, b):
    return _norm(a) == _norm(b)


def _numeric(op, target, fact):
    value = float(fact["value"])
    if not math.isfinite(value):
        return _result(UNKNOWN, "The recorded value is not a finite number.")
    lo, hi = interval(fact)
    exact = lo == hi
    ival = None if exact else [lo, hi]
    if op == "==":
        if not exact:
            return _result(UNKNOWN, "An exact match cannot be shown for a predicted value.", value, None, ival)
        ok = math.isclose(value, float(target), rel_tol=1e-9, abs_tol=1e-12)
        return _result(SATISFIED if ok else VIOLATED, f"Recorded value {_fmt(value)}.", value, value - float(target))
    if op == "between":
        a, b = float(target[0]), float(target[1])
        margin = min(value - a, b - value)
        point = a <= value <= b
        if lo >= a and hi <= b:
            state = SATISFIED
        elif hi < a or lo > b:
            state = VIOLATED
        else:
            state = UNKNOWN
    else:
        t = float(target)
        if op == ">=":
            margin, point = value - t, value >= t
            state = SATISFIED if lo >= t else VIOLATED if hi < t else UNKNOWN
        elif op == ">":
            margin, point = value - t, value > t
            state = SATISFIED if lo > t else VIOLATED if hi <= t else UNKNOWN
        elif op == "<=":
            margin, point = t - value, value <= t
            state = SATISFIED if hi <= t else VIOLATED if lo > t else UNKNOWN
        else:
            margin, point = t - value, value < t
            state = SATISFIED if hi < t else VIOLATED if lo >= t else UNKNOWN
    if exact:
        detail = f"Value {_fmt(value)}."
    elif state == UNKNOWN:
        detail = (f"Predicted {_fmt(value)}, model interval {_fmt(lo)} to {_fmt(hi)}. The interval crosses the limit, "
                  "so the constraint is neither shown to be met nor shown to be missed.")
    else:
        detail = f"Predicted {_fmt(value)}, model interval {_fmt(lo)} to {_fmt(hi)}."
    return _result(state, detail, value, margin, ival, point if not exact else None)
