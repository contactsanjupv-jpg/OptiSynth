"""
Honest surrogate-model quality metrics via k-fold cross-validation -- NOT
computed on training data, so this reflects genuine held-out predictive
performance rather than an optimistic in-sample fit. Used by the "Model
Performance" product page.
"""
import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize


def compute_model_metrics(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0) -> dict:
    """
    Returns:
      r2_score: coefficient of determination on held-out folds (can be
        negative if the model predicts worse than the mean).
      mean_absolute_error: MAE in the target's original units.
      prediction_accuracy_pct: % of held-out points whose prediction fell
        within 10% of the observed target range -- an explicit, documented
        tolerance band, not an opaque "accuracy" figure.
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

    lo, hi = feature_bounds(X)
    y_range = max(y.max() - y.min(), 1e-9)
    tolerance = 0.10 * y_range

    preds = np.full(n, np.nan)
    for i in range(k):
        test_idx = folds[i]
        train_idx = np.concatenate([folds[j] for j in range(k) if j != i])
        if len(train_idx) < 3:
            continue
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
        "n_folds_used": k,
        "n_points_scored": int(len(y_valid)),
    }

def compute_uncertainty_calibration(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0) -> dict:
    """
    Tests whether the GP's predicted uncertainty (sigma) actually carries
    information about real error, rather than assuming it does. Returns:
      sigma_error_correlation: Spearman correlation between held-out sigma
        and |actual error| -- positive and meaningfully > 0 means higher
        predicted uncertainty genuinely associates with larger error.
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

    lo, hi = feature_bounds(X)
    preds = np.full(n, np.nan)
    sigmas = np.full(n, np.nan)
    for i in range(k):
        test_idx = folds[i]
        train_idx = np.concatenate([folds[j] for j in range(k) if j != i])
        if len(train_idx) < 3:
            continue
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

    def _spearman(a, b):
        ra = a.argsort().argsort().astype(float)
        rb = b.argsort().argsort().astype(float)
        if ra.std() == 0 or rb.std() == 0:
            return 0.0
        return float(np.corrcoef(ra, rb)[0, 1])

    within_1sigma = float(np.mean(errors <= sig)) * 100
    within_2sigma = float(np.mean(errors <= 2 * sig)) * 100

    return {
        "sigma_error_correlation": round(_spearman(sig, errors), 3),
        "within_1sigma_pct": round(within_1sigma, 1),
        "within_2sigma_pct": round(within_2sigma, 1),
        "n_points_scored": int(valid.sum()),
    }