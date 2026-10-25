"""
Honest surrogate-model quality metrics via k-fold cross-validation -- NOT
computed on training data, so this reflects genuine held-out predictive
performance rather than an optimistic in-sample fit. Used by the "Model
Performance" product page.
"""
import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize

# The "prediction accuracy" band: a held-out prediction counts as inside it when its error is at most this
# fraction of the dataset's observed target range. It is a DESCRIPTIVE HEURISTIC chosen for readability --
# it is not derived from any customer specification and is NOT an engineering acceptance threshold.
ACCURACY_TOLERANCE_FRACTION = 0.10
ACCURACY_TOLERANCE_NOTE = (
    "Heuristic descriptive band: 10% of this dataset's observed target range. It is not an engineering "
    "acceptance threshold."
)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """1-based ranks where tied values share the AVERAGE of the ranks they span (the standard definition used
    for Spearman's rho). `argsort().argsort()` instead gives tied values distinct, arbitrary ranks."""
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman_correlation(a, b):
    """Tie-aware Spearman rank correlation: the Pearson correlation of the average ranks.

    Returns None -- never a made-up number -- when the correlation is undefined: fewer than 2 pairs, any
    non-finite value, or either input constant (all ranks equal). Raises ValueError for different lengths."""
    a = np.asarray(a, dtype=float).ravel()
    b = np.asarray(b, dtype=float).ravel()
    if a.shape != b.shape:
        raise ValueError("spearman_correlation needs two sequences of the same length")
    if len(a) < 2 or not (np.isfinite(a).all() and np.isfinite(b).all()):
        return None
    ra, rb = _average_ranks(a), _average_ranks(b)
    da, db = ra - ra.mean(), rb - rb.mean()
    denominator = float(np.sqrt(np.sum(da ** 2) * np.sum(db ** 2)))
    if denominator == 0.0:
        return None
    return float(np.clip(np.sum(da * db) / denominator, -1.0, 1.0))


def compute_model_metrics(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0) -> dict:
    """
    Returns:
      r2_score: coefficient of determination on held-out folds (can be
        negative if the model predicts worse than the mean).
      mean_absolute_error: MAE in the target's original units.
      prediction_accuracy_pct: % of held-out points whose prediction fell
        within 10% of the observed target range. That band is a HEURISTIC
        for readability (accuracy_tolerance_band is its width in target
        units, accuracy_tolerance_note says so) -- it is NOT an engineering
        acceptance threshold and is not tied to any customer requirement.
      Feature scaling bounds are fitted on each fold's TRAINING rows only.
      n_folds_used: actual folds used (may be < k if there isn't enough data).
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    if n < 6:
        return {"error": "Need at least 6 historical rows to cross-validate model quality."}

    k = max(2, min(k, n // 2, 10))
    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    rng.shuffle(idx)
    folds = np.array_split(idx, k)

    y_range = max(y.max() - y.min(), 1e-9)
    tolerance = ACCURACY_TOLERANCE_FRACTION * y_range

    preds = np.full(n, np.nan)
    for i in range(k):
        test_idx = folds[i]
        train_idx = np.concatenate([folds[j] for j in range(k) if j != i])
        if len(train_idx) < 3:
            continue
        # Bounds come from THIS fold's training rows only: fitting them on all rows would let the held-out
        # rows influence how the model's inputs are scaled (leakage). Held-out rows may land outside [0, 1],
        # exactly as a new candidate outside the training range does in production.
        lo, hi = feature_bounds(X[train_idx])
        Xn_train = normalize(X[train_idx], lo, hi)
        Xn_test = normalize(X[test_idx], lo, hi)
        gp = build_surrogate(d).fit(Xn_train, y[train_idx])
        preds[test_idx] = gp.predict(Xn_test)

    valid = ~np.isnan(preds)
    y_valid, p_valid = y[valid], preds[valid]
    if len(y_valid) < 2:
        return {"error": "Not enough held-out predictions to score."}

    ss_res = np.sum((y_valid - p_valid) ** 2)
    ss_tot = np.sum((y_valid - y_valid.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(y_valid - p_valid)))
    within_tol = float(np.mean(np.abs(y_valid - p_valid) <= tolerance)) * 100

    return {
        "r2_score": round(float(r2), 3),
        "mean_absolute_error": round(mae, 3),
        "prediction_accuracy_pct": round(within_tol, 1),
        "accuracy_tolerance_band": round(float(tolerance), 3),
        "accuracy_tolerance_note": ACCURACY_TOLERANCE_NOTE,
        "n_folds_used": k,
        "n_points_scored": int(len(y_valid)),
    }

def compute_uncertainty_calibration(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0) -> dict:
    """
    Tests whether the GP's predicted uncertainty (sigma) actually carries
    information about real error, rather than assuming it does. Returns:
      sigma_error_correlation: tie-aware Spearman correlation between
        held-out sigma and |actual error| -- positive and meaningfully > 0
        means higher predicted uncertainty genuinely associates with larger
        error. When it is undefined (sigma or error constant) the value is
        0.0 for compatibility and sigma_error_correlation_defined is False:
        0.0 then means "could not be computed", NOT "no association".
      within_1sigma_pct / within_2sigma_pct: fraction of held-out points
        whose actual value fell within 1 / 2 predicted standard deviations
        of the prediction.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    if n < 6:
        return {"error": "Need at least 6 historical rows to evaluate uncertainty calibration."}

    k = max(2, min(k, n // 2, 10))
    rng = np.random.default_rng(seed)
    idx = np.arange(n)
    rng.shuffle(idx)
    folds = np.array_split(idx, k)

    preds = np.full(n, np.nan)
    sigmas = np.full(n, np.nan)
    for i in range(k):
        test_idx = folds[i]
        train_idx = np.concatenate([folds[j] for j in range(k) if j != i])
        if len(train_idx) < 3:
            continue
        lo, hi = feature_bounds(X[train_idx])  # this fold's training rows only (see compute_model_metrics)
        Xn_train = normalize(X[train_idx], lo, hi)
        Xn_test = normalize(X[test_idx], lo, hi)
        gp = build_surrogate(d).fit(Xn_train, y[train_idx])
        mu, sigma = gp.predict(Xn_test, return_std=True)
        preds[test_idx] = mu
        sigmas[test_idx] = sigma

    valid = ~np.isnan(preds)
    if valid.sum() < 4:
        return {"error": "Not enough held-out predictions to evaluate calibration."}

    errors = np.abs(y[valid] - preds[valid])
    sig = sigmas[valid]

    correlation = spearman_correlation(sig, errors)

    within_1sigma = float(np.mean(errors <= sig)) * 100
    within_2sigma = float(np.mean(errors <= 2 * sig)) * 100

    return {
        "sigma_error_correlation": round(correlation, 3) if correlation is not None else 0.0,
        "sigma_error_correlation_defined": correlation is not None,
        "within_1sigma_pct": round(within_1sigma, 1),
        "within_2sigma_pct": round(within_2sigma, 1),
        "n_points_scored": int(valid.sum()),
    }