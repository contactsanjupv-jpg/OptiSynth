"""
Honest surrogate-model quality metrics via k-fold cross-validation -- NOT
computed on training data, so this reflects genuine held-out predictive
performance rather than an optimistic in-sample fit. Used by the "Model
Performance" product page.
"""
import numpy as np

from engine.models.surrogate import build_surrogate
import warnings

from sklearn.exceptions import ConvergenceWarning

from engine.preprocessing.normalization import feature_bounds, normalize

# The "prediction accuracy" band: a held-out prediction counts as inside it when its error is at most this
# fraction of the dataset's observed target range. It is a DESCRIPTIVE HEURISTIC chosen for readability --
# it is not derived from any customer specification and is NOT an engineering acceptance threshold.
ACCURACY_TOLERANCE_FRACTION = 0.10
ACCURACY_TOLERANCE_NOTE = (
    "Heuristic descriptive band: 10% of this dataset's observed target range. It is not an engineering "
    "acceptance threshold."
)


MIN_GROUPS_FOR_GROUPED_CV = 6
NOISE_LOWER_BOUND = 1e-4  # must match WhiteKernel noise_level_bounds in engine/models/surrogate.py


def _make_folds(n: int, k: int, seed: int, groups=None):
    """Row-index folds. groups=None: shuffled rows (the original behaviour, unchanged). Otherwise every row
    with the same group label goes to the SAME fold, so no group is ever on both sides of a split. The caller
    decides what a group is; this function never invents one."""
    rng = np.random.default_rng(seed)
    if groups is None:
        idx = np.arange(n)
        rng.shuffle(idx)
        return np.array_split(idx, k)
    ids = {}
    gid = np.array([ids.setdefault(g, len(ids)) for g in groups])
    sizes = np.bincount(gid)
    # Balance rows per fold without ever splitting a group: place the largest groups first, each into the fold that
    # currently holds the fewest rows. The seeded shuffle only decides the order among equal-sized groups, so the
    # result is deterministic for a given seed. (Round-robin by group COUNT left folds very uneven when replicate
    # counts differ.) A single group larger than an average fold cannot be balanced and simply fills its own fold.
    order = sorted(rng.permutation(len(ids)).tolist(), key=lambda g: -int(sizes[g]))
    loads = [0] * k
    fold_of_group = np.empty(len(ids), dtype=int)
    for g in order:
        f = min(range(k), key=lambda i: loads[i])
        fold_of_group[g] = f
        loads[f] += int(sizes[g])
    row_fold = fold_of_group[gid]
    return [np.flatnonzero(row_fold == i) for i in range(k)]


def _check_groups(groups, n: int):
    """None when no groups were given; an error dict when they cannot support grouped validation."""
    if groups is None:
        return None
    if len(groups) != n:
        raise ValueError("groups must have one label per row")
    n_groups = len(set(groups))
    if n_groups < MIN_GROUPS_FOR_GROUPED_CV:
        return {"error": f"Need at least {MIN_GROUPS_FOR_GROUPED_CV} distinct input sets to cross-validate with "
                         f"repeated input sets kept together (found {n_groups})."}
    return None


def _new_fit_diagnostics() -> dict:
    return {"n_fits": 0, "n_convergence_warnings": 0, "n_fits_with_hyperparameter_at_bound": 0,
            "n_fits_noise_at_lower_bound": 0}


def _fit_recorded(dim: int, Xn, y, diag: dict):
    """Fit the surrogate, RECORD what the optimiser reported (and re-emit it, never swallow it)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        gp = build_surrogate(dim).fit(Xn, y)
    for w in caught:
        warnings.warn(w.message, w.category, stacklevel=2)
    diag["n_fits"] += 1
    if any(issubclass(w.category, ConvergenceWarning) for w in caught):
        diag["n_convergence_warnings"] += 1
    fitted_kernel = getattr(gp, "kernel_", None)  # absent on stand-in models used in tests
    if fitted_kernel is None:
        return gp
    theta, bounds = fitted_kernel.theta, fitted_kernel.bounds
    if (np.isclose(theta, bounds[:, 0], atol=1e-3) | np.isclose(theta, bounds[:, 1], atol=1e-3)).any():
        diag["n_fits_with_hyperparameter_at_bound"] += 1
    noise = getattr(getattr(fitted_kernel, "k2", None), "noise_level", None)
    if noise is not None and noise <= NOISE_LOWER_BOUND * 1.01:
        diag["n_fits_noise_at_lower_bound"] += 1
    return gp


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


def compute_model_metrics(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0, groups=None) -> dict:
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
      groups: optional one label per row. When given, rows sharing a label are never split across folds
        (cv_scheme "grouped", n_groups reported); otherwise folds are random rows (cv_scheme "random_rows"),
        which is optimistic when replicate rows exist. fit_diagnostics records optimiser warnings.
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    if n < 6:
        return {"error": "Need at least 6 historical rows to cross-validate model quality."}

    problem = _check_groups(groups, n)
    if problem:
        return problem
    k = max(2, min(k, (len(set(groups)) if groups is not None else n) // 2, 10))
    folds = _make_folds(n, k, seed, groups)
    diag = _new_fit_diagnostics()

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
        gp = _fit_recorded(d, Xn_train, y[train_idx], diag)
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
        "cv_scheme": "grouped" if groups is not None else "random_rows",
        "n_groups": len(set(groups)) if groups is not None else None,
        "fit_diagnostics": diag,
    }

def compute_uncertainty_calibration(X: np.ndarray, y: np.ndarray, k: int = 5, seed: int = 0, groups=None) -> dict:
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

    problem = _check_groups(groups, n)
    if problem:
        return problem
    k = max(2, min(k, (len(set(groups)) if groups is not None else n) // 2, 10))
    folds = _make_folds(n, k, seed, groups)
    diag = _new_fit_diagnostics()

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
        gp = _fit_recorded(d, Xn_train, y[train_idx], diag)
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
        "cv_scheme": "grouped" if groups is not None else "random_rows",
        "fit_diagnostics": diag,
    }