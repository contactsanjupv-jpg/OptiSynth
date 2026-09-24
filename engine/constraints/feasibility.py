"""
Constraint handling. A constraint is {column, op ('<=' or '>='), value}
against a secondary measured quantity (e.g. viscosity must stay <= 5.0 for
the formulation to be processable). Because the constraint value for an
unevaluated candidate is unknown, each constraint gets its own surrogate
model, and feasibility is expressed probabilistically -- P(constraint
satisfied) -- rather than as a hard yes/no filter, consistent with how the
target-property surrogate expresses uncertainty.
"""
import numpy as np
from scipy.stats import norm

from engine.models.surrogate import build_surrogate


def fit_constraint_models(Xn: np.ndarray, constraints: list, constraint_data: dict, random_state: int = 0) -> dict:
    """Fits one surrogate per constraint column that actually has data.
    Returns {column: fitted_gp}."""
    models = {}
    if not constraints or not constraint_data:
        return models
    dim = Xn.shape[1]
    for c in constraints:
        col = c["column"]
        if col in constraint_data:
            values = np.asarray(constraint_data[col], dtype=float)
            models[col] = build_surrogate(dim, random_state=random_state).fit(Xn, values)
    return models


def feasibility_probability(constraint_models: dict, Xn_candidates: np.ndarray, constraints: list) -> np.ndarray:
    """Returns, per candidate, the probability that ALL constraints are
    jointly satisfied (independence assumed across constraints -- a
    simplification worth revisiting if constraints are known to correlate)."""
    n = len(Xn_candidates)
    if not constraints:
        return np.ones(n)

    p_all = np.ones(n)
    for c in constraints:
        gp = constraint_models.get(c["column"])
        if gp is None:
            continue
        mu, sigma = gp.predict(Xn_candidates, return_std=True)
        sigma = np.maximum(sigma, 1e-9)
        if c["op"] == "<=":
            p = norm.cdf((c["value"] - mu) / sigma)
        elif c["op"] == ">=":
            p = norm.cdf((mu - c["value"]) / sigma)
        else:
            raise ValueError(f"Unsupported constraint op: {c['op']!r}")
        p_all *= p
    return p_all