"""
Retrospective backtest on the customer's OWN historical data -- no physical
experiments needed, no synthetic benchmark. Replays their dataset twice:
once in the order they actually ran it, and once in the order the engine
would have chosen (picking, at each step, the highest-acquisition-score
experiment from the remaining unused historical rows). Comparing
"experiments needed to reach your own best result" between the two
orderings is real evidence grounded entirely in data the customer already
collected -- this is the ROI proof shown on the Model Performance page and
in the generated report.
"""
import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize
from engine.acquisition.expected_improvement import expected_improvement
from engine.constraints.feasibility import fit_constraint_models, feasibility_probability
from engine.optimization.direction import best_so_far, is_better, target_reached


def run_backtest(X, y, direction, constraints=None, constraint_data=None,
                  target=None, n_init=5, seed=0) -> dict:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    if n < n_init + 2:
        return {"error": "Not enough historical rows for a meaningful backtest (need at least n_init + 2)."}

    lo, hi = feature_bounds(X)
    Xn = normalize(X, lo, hi)

    if target is None:
        target = best_so_far(y, direction)
        target = target * 0.98 if direction == "maximize" and target > 0 else target

    # --- order the customer actually ran ---
    best = -np.inf if direction == "maximize" else np.inf
    actual_evals = None
    actual_curve = []
    for i in range(n):
        v = y[i]
        if best in (np.inf, -np.inf) or is_better(v, best, direction):
            best = v
        actual_curve.append(float(best))
        if actual_evals is None and target_reached(best, target, direction):
            actual_evals = i + 1

    # --- order the engine would have chosen, restricted to the historical pool ---
    rng = np.random.default_rng(seed)
    remaining = list(range(n))
    rng.shuffle(remaining)
    used = remaining[:n_init]
    remaining = [i for i in remaining if i not in used]

    engine_best = best_so_far(y[used], direction)
    engine_evals = n_init if target_reached(engine_best, target, direction) else None
    engine_curve = [engine_best] * n_init

    while remaining:
        surrogate = build_surrogate(d).fit(Xn[used], y[used])
        cand_idx = np.array(remaining)
        mu, sigma = surrogate.predict(Xn[cand_idx], return_std=True)

        feas = np.ones(len(cand_idx))
        if constraints and constraint_data:
            models = fit_constraint_models(Xn[used], constraints, {
                col: np.asarray(vals, dtype=float)[used] for col, vals in constraint_data.items()
            })
            feas = feasibility_probability(models, Xn[cand_idx], constraints)

        acq = expected_improvement(mu, sigma, engine_best, direction) * feas
        pick_pos = int(np.argmax(acq))
        pick_idx = cand_idx[pick_pos]

        used.append(pick_idx)
        remaining.remove(pick_idx)
        v = y[pick_idx]
        if is_better(v, engine_best, direction):
            engine_best = v
        engine_curve.append(float(engine_best))
        if engine_evals is None and target_reached(engine_best, target, direction):
            engine_evals = len(used)

    return {
        "target": float(target),
        "actual_order_evals": actual_evals,
        "engine_order_evals": engine_evals,
        "n_historical_rows": n,
        "reduction_pct": (round((1 - engine_evals / actual_evals) * 100, 1)
                          if actual_evals and engine_evals else None),
        # Running-best value after each experiment, for plotting "optimization
        # progress" curves. Both arrays have length n.
        "actual_order_curve": actual_curve,
        "engine_order_curve": engine_curve,
    }
