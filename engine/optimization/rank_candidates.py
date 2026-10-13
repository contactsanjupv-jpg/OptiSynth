"""
Candidate-substitute ranking: given historical qualification data and a
SMALL, CUSTOMER-SUPPLIED list of named candidates (NOT a generated pool --
see recommend_next's docstring for that distinction), predict each
candidate's probability of matching the target spec, with uncertainty.

This is deliberately NOT candidate discovery/generation. The product this
serves (the forced-substitution qualification diagnostic) only ever
scores candidates the customer already names -- any number of known
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
    candidates: list of dicts, each {"id": <stable identifier>, "name": str,
      "features": {col: value}} -- SPECIFIC, CUSTOMER-NAMED points, never
      generated internally. "id" is the candidate's stable identity: it is
      echoed back on every result and is what ties are broken on. "name" is
      only a display label -- two candidates may even share one at this
      level without being confused, because results are built per input
      position, never looked up by name. "id" is optional here (callers
      that have none get input position as the final tie-break).
    seed: passed straight to build_surrogate's random_state (see that
      module's docstring for why this must never be left as sklearn's
      default None).

    Returns candidates ranked best-first, each augmented with:
      id, predicted_value, uncertainty_std, predicted_probability,
      model_version.

    Ordering is fully deterministic (see ranking_sort_key): highest
    predicted_probability first; ties are broken by the predicted value in
    the requirement's favorable direction (higher for 'maximize', lower for
    'minimize'); any remaining tie is broken by candidate id (or input
    position when no ids were supplied).

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

    # Identical inputs must get identical estimates. Predicting a batch can differ in the last few
    # floating-point digits depending on a row's position in it, which would make two candidates with
    # the same inputs look different (and so rank by noise). Each DISTINCT input row is therefore
    # predicted once and the result shared by every candidate that has that row.
    unique_rows, inverse = np.unique(cand_n, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    mu_u, sigma_u = surrogate.predict(unique_rows, return_std=True)
    mu, sigma = mu_u[inverse], sigma_u[inverse]

    have_ids = all(c.get("id") is not None for c in candidates)

    results = []
    for i, c in enumerate(candidates):
        prob = _probability_meets_target(mu[i], sigma[i], target_value, direction)
        results.append({
            "id": c.get("id"),
            "name": c["name"],
            "features": c["features"],
            "predicted_value": float(mu[i]),
            "uncertainty_std": float(sigma[i]),
            "predicted_probability": float(prob),
            "model_version": "gp-rank-v1",
            "_input_position": i,
        })

    results.sort(key=lambda r: ranking_sort_key(
        r["predicted_probability"], r["predicted_value"],
        r["id"] if have_ids else r["_input_position"], direction,
    ))
    for r in results:
        del r["_input_position"]
    return results


# Probabilities and predicted values that agree to this many significant
# digits are TIES. Two candidates with identical inputs go through identical
# arithmetic but can differ in the last floating-point bits depending on how
# the batch is evaluated; without this, which of them "wins" would be noise.
_TIE_SIGNIFICANT_DIGITS = 12


def _quantize(x: float) -> float:
    return float(f"{x:.{_TIE_SIGNIFICANT_DIGITS}g}")


def ranking_sort_key(predicted_probability: float, predicted_value, tiebreak, direction: str) -> tuple:
    """The single definition of ranking order, used wherever candidates are
    ordered (the ranking run itself, the stored-prediction listing and the
    report) so they can never disagree.

    Sorts ascending on: (1) negated model-estimated probability -- most likely
    to meet the requirement first; (2) the predicted value in the
    requirement's FAVORABLE direction -- highest first for 'maximize', lowest
    first for 'minimize'; a missing predicted value (a prediction stored
    before values were recorded) sorts after any recorded one; (3) the
    candidate's stable id (or input position). Never the candidate's name."""
    prob_key = -_quantize(float(predicted_probability))
    if predicted_value is None:
        value_key = float("inf")
    else:
        v = _quantize(float(predicted_value))
        value_key = -v if direction == "maximize" else v
    return (prob_key, value_key, tiebreak)


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
