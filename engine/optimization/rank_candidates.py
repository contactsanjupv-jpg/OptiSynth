"""
Candidate-substitute ranking: given historical qualification data and a
SMALL, CUSTOMER-SUPPLIED list of named candidates (NOT a generated pool --
see recommend_next's docstring for that distinction), predict each
candidate's probability of matching the target spec, with uncertainty.

This is deliberately NOT candidate discovery/generation. The product this
serves (the forced-substitution qualification diagnostic) only ever
scores candidates the customer already names -- typically 3-5 known
alternative materials -- never proposes new ones. Reuses the exact same
GP surrogate and normalization machinery as recommend_next and
compute_model_metrics (engine/models/surrogate.py,
engine/preprocessing/normalization.py) -- the only new logic here is
scoring caller-supplied points instead of a generated candidate pool.
"""
import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize


def rank_candidates(
    X: np.ndarray,
    y: np.ndarray,
    direction: str,
    feature_names: list,
    target_value: float,
    candidates: list,
    seed: int = 0,
) -> list:
    """
    X: (n, d) ndarray of historical qualification feature values (raw units).
    y: (n,) ndarray of historical qualification outcome values (raw units).
    direction: 'maximize' or 'minimize' -- which side of target_value counts
      as passing (mirrors the existing convention in engine/optimization/direction.py).
    feature_names: the d feature column names, in X's column order.
    target_value: the spec threshold a candidate must meet.
    candidates: list of dicts, each {"name": str, "features": {col: value}} --
      SPECIFIC, CUSTOMER-NAMED points, never generated internally.
    seed: passed straight to build_surrogate's random_state (see that
      module's docstring for why this must never be left as sklearn's
      default None).

    Returns candidates ranked best-first (highest predicted_probability
    first), each augmented with:
      predicted_value, uncertainty_std, predicted_probability,
      model_version.

    predicted_probability is P(candidate meets target_value | direction),
    computed from the GP's own predictive normal distribution (mu, sigma)
    via the standard normal CDF -- NOT a heuristic score. If sigma is
    numerically ~0 (e.g. a candidate sits exactly on already-observed
    data), the probability collapses to a hard 0/1 based on whether mu
    already clears target_value, which is the correct limiting behavior.
    """
    if len(candidates) == 0:
        raise ValueError("rank_candidates requires at least one candidate.")

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    d = X.shape[1]

    lo, hi = feature_bounds(X)
    Xn = normalize(X, lo, hi)

    surrogate = build_surrogate(d, random_state=seed).fit(Xn, y)

    cand_raw = np.array(
        [[c["features"][feature_names[j]] for j in range(d)] for c in candidates],
        dtype=float,
    )
    cand_n = normalize(cand_raw, lo, hi)

    mu, sigma = surrogate.predict(cand_n, return_std=True)

    results = []
    for i, c in enumerate(candidates):
        prob = _probability_meets_target(mu[i], sigma[i], target_value, direction)
        results.append({
            "name": c["name"],
            "features": c["features"],
            "predicted_value": float(mu[i]),
            "uncertainty_std": float(sigma[i]),
            "predicted_probability": float(prob),
            "model_version": "gp-rank-v1",
        })

    results.sort(key=lambda r: r["predicted_probability"], reverse=True)
    return results


def _probability_meets_target(mu: float, sigma: float, target_value: float, direction: str) -> float:
    """P(prediction meets or exceeds target_value) for 'maximize', or
    P(prediction meets or is below target_value) for 'minimize', under the
    GP's own posterior normal N(mu, sigma^2). Uses the standard normal CDF
    directly (via erf) rather than scipy, so this file has no dependency
    beyond numpy -- consistent with the rest of engine/ minimizing external
    dependencies to exactly what each module needs."""
    if sigma <= 1e-12:
        if direction == "maximize":
            return 1.0 if mu >= target_value else 0.0
        return 1.0 if mu <= target_value else 0.0

    z = (target_value - mu) / sigma
    # Standard normal CDF via the error function (numpy has erf-free math,
    # but this closed form avoids adding a scipy dependency to this one
    # small function when the rest of the file only needs numpy).
    cdf_z = 0.5 * (1.0 + _erf(z / np.sqrt(2.0)))
    if direction == "maximize":
        return float(1.0 - cdf_z)
    return float(cdf_z)


def _erf(x: np.ndarray) -> np.ndarray:
    """Abramowitz & Stegun 7.1.26 approximation, max error ~1.5e-7 --
    ample precision for a probability estimate that's already an
    approximation of a GP posterior, and avoids pulling in scipy.special
    for one function."""
    sign = np.sign(x)
    x = np.abs(x)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * np.exp(-x * x)
    return sign * y
