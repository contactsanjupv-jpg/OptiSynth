"""Scores for held out predictions. All inputs are held out values: nothing here sees training data."""
import math

import numpy as np
from scipy.stats import norm

from engine.evaluation.model_metrics import spearman_correlation

NOMINAL_LEVELS = (0.50, 0.80, 0.90, 0.95)
MIN_POINTS_FOR_CALIBRATION_READING = 30

COVERAGE_CAVEAT = (
    "Observed coverage matching the nominal level does not establish calibration. Intervals can cover the right "
    "share of points while being too wide to be useful (see width), coverage can differ across the input space, and "
    "held out rows that share inputs or sources are not independent, so the real sampling uncertainty is larger than "
    "the interval shown."
)


def wilson_interval(k: int, n: int, z: float = 1.96):
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def gaussian_crps(y, mu, sigma) -> np.ndarray:
    """Continuous ranked probability score for a normal prediction, in target units. Lower is better. It rewards
    intervals that are both right and narrow, so it cannot be improved by widening alone."""
    y, mu, sigma = (np.asarray(a, dtype=float) for a in (y, mu, sigma))
    safe = np.where(sigma > 1e-12, sigma, 1.0)
    z = (y - mu) / safe
    crps = safe * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / math.sqrt(math.pi))
    return np.where(sigma > 1e-12, crps, np.abs(y - mu))


def error_metrics(y, mu, baseline_mu) -> dict:
    y, mu, base = (np.asarray(a, dtype=float) for a in (y, mu, baseline_mu))
    resid = y - mu
    n = len(y)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    rmse = math.sqrt(ss_res / n)
    base_rmse = math.sqrt(float(np.sum((y - base) ** 2)) / n)
    return {
        "n": int(n),
        "mae": float(np.mean(np.abs(resid))),
        "rmse": rmse,
        "r2": (1 - ss_res / ss_tot) if ss_tot > 0 else None,
        "bias": float(np.mean(-resid)),  # mean of (predicted - measured)
        "median_absolute_error": float(np.median(np.abs(resid))),
        "max_absolute_error": float(np.max(np.abs(resid))),
        "target_range_evaluated": float(y.max() - y.min()),
        "baseline_rmse": base_rmse,
        "skill_vs_mean_baseline": (1 - rmse / base_rmse) if base_rmse > 0 else None,
    }


def residual_summary(y, mu) -> dict:
    resid = np.asarray(y, dtype=float) - np.asarray(mu, dtype=float)
    q = np.quantile(resid, [0.05, 0.25, 0.5, 0.75, 0.95])
    out = {"n": int(len(resid)), "mean": float(resid.mean()), "sd": float(resid.std(ddof=1)) if len(resid) > 1 else None,
           "quantiles": {"p05": float(q[0]), "p25": float(q[1]), "p50": float(q[2]), "p75": float(q[3]), "p95": float(q[4])},
           "by_predicted_level": []}
    if len(resid) >= 15:  # three bands of at least five points: a trend in the residuals is only worth showing then
        order = np.argsort(np.asarray(mu, dtype=float), kind="mergesort")
        for label, idx in zip(("lowest third", "middle third", "highest third"), np.array_split(order, 3)):
            out["by_predicted_level"].append({"band": label, "n": int(len(idx)), "mean_residual": float(resid[idx].mean()),
                                              "mean_absolute_residual": float(np.abs(resid[idx]).mean())})
    return out


def interval_table(y, mu, sigma, levels=NOMINAL_LEVELS) -> list:
    y, mu, sigma = (np.asarray(a, dtype=float) for a in (y, mu, sigma))
    n = len(y)
    rows = []
    for level in levels:
        z = float(norm.ppf(0.5 + level / 2))
        inside = int(np.sum(np.abs(y - mu) <= z * sigma))
        width = float(np.mean(2 * z * sigma))
        span = float(y.max() - y.min())
        ci = wilson_interval(inside, n)
        observed = inside / n
        if n < MIN_POINTS_FOR_CALIBRATION_READING:
            reading = "insufficient_samples"
        elif ci[1] < level:
            reading = "under_covering"       # the intervals are too narrow: real values fall outside more often
        elif ci[0] > level:
            reading = "over_covering"        # the intervals are wider than they need to be
        else:
            reading = "no_clear_departure"
        rows.append({"nominal": level, "n": n, "inside": inside, "observed": observed, "observed_interval_95": ci,
                     "mean_width": width, "mean_width_relative_to_range": (width / span) if span > 0 else None,
                     "reading": reading})
    return rows


def calibration_summary(y, mu, sigma) -> dict:
    y, mu, sigma = (np.asarray(a, dtype=float) for a in (y, mu, sigma))
    n = len(y)
    table = interval_table(y, mu, sigma)
    readings = [r["reading"] for r in table]
    if n < MIN_POINTS_FOR_CALIBRATION_READING:
        assessment = "insufficient_samples"
    elif "under_covering" in readings:
        assessment = "under_covering"
    elif "over_covering" in readings:
        assessment = "over_covering"
    else:
        assessment = "no_clear_departure"
    positive = sigma > 1e-12
    z = (y[positive] - mu[positive]) / sigma[positive]
    rho = spearman_correlation(sigma, np.abs(y - mu)) if n >= 4 else None
    return {
        "n": int(n),
        "levels": table,
        "assessment": assessment,
        "max_gap": float(max(abs(r["observed"] - r["nominal"]) for r in table)),
        "standardised_residuals": {"n": int(positive.sum()), "mean": float(z.mean()) if len(z) else None,
                                   "sd": float(z.std(ddof=1)) if len(z) > 1 else None,
                                   "note": "Close to 0 and 1 would be expected of well scaled intervals; the sample size limits how closely."},
        "mean_crps": float(gaussian_crps(y, mu, sigma).mean()),
        "sigma_error_rank_correlation": rho,
        "sharpness": {"mean_sigma": float(sigma.mean()), "median_sigma": float(np.median(sigma)),
                      "mean_sigma_relative_to_range": (float(sigma.mean() / (y.max() - y.min())) if y.max() > y.min() else None)},
        "caveat": COVERAGE_CAVEAT,
        "recalibration": "none applied: these are the model's own intervals, reported as they are",
    }
