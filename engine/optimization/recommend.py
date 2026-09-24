"""
Live recommendation: given all experiments run so far, recommend the next
N candidates worth running, with predicted value, uncertainty, and
feasibility. This is the "what should I run next" product feature exposed
by POST /api/projects/{id}/recommend.
"""
from typing import Optional

import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize, denormalize
from engine.acquisition.expected_improvement import expected_improvement
from engine.constraints.feasibility import fit_constraint_models, feasibility_probability
from engine.optimization.candidate_generation import generate_candidate_pool
from engine.optimization.direction import best_so_far


def recommend_next(
    X: np.ndarray,
    y: np.ndarray,
    direction: str,
    feature_names: list,
    constraints: Optional[list] = None,
    constraint_data: Optional[dict] = None,
    n_recommend: int = 5,
    candidate_pool: int = 3000,
    seed: int = 0,
) -> list:
    """
    X: (n, d) ndarray of historical feature values (raw units)
    y: (n,) ndarray of historical target values
    Returns a list of dicts, ranked best-first, each with:
      features, predicted_value, uncertainty_std, feasibility_probability,
      acquisition_score.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape

    lo, hi = feature_bounds(X)
    Xn = normalize(X, lo, hi)

    surrogate = build_surrogate(d).fit(Xn, y)
    constraint_models = fit_constraint_models(Xn, constraints or [], constraint_data or {})

    cand_n = generate_candidate_pool(d, candidate_pool, seed)
    cand_raw = denormalize(cand_n, lo, hi)

    mu, sigma = surrogate.predict(cand_n, return_std=True)
    feas = feasibility_probability(constraint_models, cand_n, constraints or [])
    current_best = best_so_far(y, direction)
    acq = expected_improvement(mu, sigma, current_best, direction) * feas

    top_idx = np.argsort(-acq)[:n_recommend]
    results = []
    for idx in top_idx:
        results.append({
            "features": {feature_names[j]: float(cand_raw[idx, j]) for j in range(d)},
            "predicted_value": float(mu[idx]),
            "uncertainty_std": float(sigma[idx]),
            "feasibility_probability": float(feas[idx]),
            "acquisition_score": float(acq[idx]),
        })
    return results
