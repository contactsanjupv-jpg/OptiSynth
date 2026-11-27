"""Runs one validation protocol and returns a plain, JSON ready result.

Leakage rules, each covered by a test:
  * Within every fold, scaling bounds, the applicability reference distribution and its thresholds, and the mean
    baseline come from that fold's TRAINING rows only.
  * The Gaussian process is fitted on training rows only. No held out row, and no held out target, reaches any
    fit, scaling, threshold or baseline for the result reported for that row.
  * No recalibration is fitted anywhere. Intervals are the model's own, so there is no calibration step that could
    see the evaluation data.
  * Region clusters (leave one region out) are built from input values alone and only decide which rows are held
    out together.
A failed or incomplete run returns no metrics. It never returns a status that reads as success."""
import hashlib
import json
import warnings

import numpy as np

from engine.evaluation.model_metrics import _fit_recorded, _fold_seed, _make_folds, _new_fit_diagnostics
from engine.preprocessing.normalization import feature_bounds, normalize
from engine.validation import applicability as app
from engine.validation import protocols as P
from engine.validation import scoring
from engine.validation.identity import canonical_number

COMPLETED = "completed"
WARNINGS = "completed_with_warnings"
INSUFFICIENT = "insufficient_samples"
FAILED = "failed"
NOT_AVAILABLE = "not_available"
MAX_POINTS_STORED = 1000


def _result(key, status, message=None, **extra):
    return {"protocol": {"key": key, "version": P.VERSION, "hash": P.protocol_hash(key)}, "status": status,
            "message": message, "warnings": [], "counts": None, "metrics": None, "calibration": None,
            "applicability_check": None, "points": None, "fit_diagnostics": None, "evaluation_rows_hash": None,
            "fold_assignment_hash": None, **extra}


def _hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def region_clusters(X: np.ndarray):
    """Deterministic farthest point clusters of the DISTINCT input vectors, from input values only.
    Returns (cluster label per row of X, number of clusters) or (None, 0) when there are too few distinct inputs."""
    X = np.asarray(X, dtype=float)
    U, inverse = np.unique(X, axis=0, return_inverse=True)
    inverse = np.asarray(inverse).reshape(-1)
    c = min(P.MAX_CLUSTERS, len(U) // 3)
    if c < 3:
        return None, 0
    lo, hi = feature_bounds(U)
    Un = normalize(U, lo, hi)
    first = int(np.argmax(np.sqrt(((Un - Un.mean(axis=0)) ** 2).sum(axis=1))))
    seeds = [first]
    nearest = np.sqrt(((Un - Un[first]) ** 2).sum(axis=1))
    while len(seeds) < c:
        nxt = int(np.argmax(nearest))
        seeds.append(nxt)
        nearest = np.minimum(nearest, np.sqrt(((Un - Un[nxt]) ** 2).sum(axis=1)))
    dist = np.stack([np.sqrt(((Un - Un[s]) ** 2).sum(axis=1)) for s in seeds], axis=1)
    label_of_unique = np.argmin(dist, axis=1)
    return label_of_unique[inverse], c


def _folds_for(key, X, groups, seed):
    n = len(X)
    if key == P.RANDOM_ROWS:
        k = max(2, min(P.MAX_FOLDS, n // 2))
        return _make_folds(n, k, seed, None), None
    if key == P.GROUPED_INPUT_SETS:
        n_groups = len(set(groups))
        k = max(2, min(P.MAX_FOLDS, n_groups // 2))
        return _make_folds(n, k, seed, groups), n_groups
    labels, c = region_clusters(X)
    if labels is None:
        return None, 0
    return [np.flatnonzero(labels == i) for i in range(c)], c


def run_protocol(key: str, X, y, groups=None, seed: int = 0) -> dict:
    """X: (n, d) raw inputs; y: (n,) targets; both in the caller's canonical row order. groups: one label per row
    (rows with identical inputs share one); needed for the grouped protocol."""
    if key not in P.DEFINITIONS:
        raise ValueError(f"Unknown validation protocol: {key}")
    definition = P.definition(key)
    if key == P.RECORDED_UNIT:
        return _result(key, NOT_AVAILABLE, definition["unavailable_reason"])
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, d = X.shape
    if not (np.isfinite(X).all() and np.isfinite(y).all()):
        return _result(key, FAILED, "The evidence contains a value that is not a finite number, so nothing was evaluated.")
    if groups is None:
        ids = {}
        groups = [ids.setdefault(tuple(r), len(ids)) for r in X.tolist()]
    n_input_sets = len(set(groups))
    need = definition["minimum"]
    if n < need["rows"]:
        return _result(key, INSUFFICIENT, f"This protocol needs at least {need['rows']} rows. The evidence has {n}.")
    if need["input_sets"] and n_input_sets < need["input_sets"]:
        return _result(key, INSUFFICIENT, f"This protocol needs at least {need['input_sets']} distinct input sets. "
                                          f"The evidence has {n_input_sets}.")
    folds, n_groups = _folds_for(key, X, groups, seed)
    if folds is None:
        return _result(key, INSUFFICIENT, "The distinct input sets cannot be split into at least three regions.")

    diag = _new_fit_diagnostics()
    pts = {name: np.full(n, np.nan) for name in ("mu", "sigma", "base", "dist")}
    fold_of = np.full(n, -1)
    app_class = np.array([app.NOT_ASSESSED] * n, dtype=object)
    fold_info, skipped = [], 0
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # optimiser warnings are counted in fit_diagnostics, not lost
            for i, test_idx in enumerate(folds):
                test_idx = np.asarray(test_idx, dtype=int)
                train_idx = np.setdiff1d(np.arange(n), test_idx)
                train_sets = len({groups[j] for j in train_idx}) if len(train_idx) else 0
                if len(test_idx) == 0 or train_sets < P.MIN_TRAIN_INPUT_SETS:
                    skipped += 1
                    fold_info.append({"fold": i, "n_test": int(len(test_idx)), "n_train": int(len(train_idx)), "status": "skipped",
                                      "reason": f"fewer than {P.MIN_TRAIN_INPUT_SETS} distinct training input sets"})
                    continue
                lo, hi = feature_bounds(X[train_idx])
                Xn_train, Xn_test = normalize(X[train_idx], lo, hi), normalize(X[test_idx], lo, hi)
                gp = _fit_recorded(d, Xn_train, y[train_idx], diag, _fold_seed(seed, i))
                mu, sigma = gp.predict(Xn_test, return_std=True)
                pts["mu"][test_idx], pts["sigma"][test_idx] = mu, sigma
                pts["base"][test_idx] = y[train_idx].mean()
                assessed, limits = app.assess_points(Xn_test, Xn_train)
                for j, a in zip(test_idx, assessed):
                    pts["dist"][j] = a["nearest_distance"]
                    app_class[j] = a["class"]
                fold_of[test_idx] = i
                fold_info.append({"fold": i, "n_test": int(len(test_idx)), "n_train": int(len(train_idx)), "status": "used",
                                  "applicability_thresholds": limits})
    except Exception as exc:  # a fit can fail for numerical reasons; say so, never report partial numbers as a result
        return _result(key, FAILED, f"The model could not be fitted for one of the validation folds ({type(exc).__name__}). "
                                    "No metrics were recorded.", fit_diagnostics=diag)

    scored = ~np.isnan(pts["mu"])
    n_scored = int(scored.sum())
    counts = {"n_rows": int(n), "n_input_sets": int(n_input_sets), "n_groups": n_groups, "n_folds": len(folds),
              "n_folds_used": len(folds) - skipped, "n_folds_skipped": skipped, "n_scored": n_scored, "folds": fold_info}
    if n_scored < 4:
        return _result(key, INSUFFICIENT, "Too few held out rows could be scored to report any metric.", counts=counts,
                       fit_diagnostics=diag)

    ys, mus, sigs, bases = y[scored], pts["mu"][scored], pts["sigma"][scored], pts["base"][scored]
    metrics = scoring.error_metrics(ys, mus, bases)
    metrics["residuals"] = scoring.residual_summary(ys, mus)
    calibration = scoring.calibration_summary(ys, mus, sigs)

    classes = app_class[scored]
    by_class = []
    for cls in app.CLASSES:
        sel = classes == cls
        if sel.any():
            err = np.abs(ys[sel] - mus[sel])
            row = {"class": cls, "n": int(sel.sum()), "median_absolute_error": float(np.median(err)),
                   "mean_absolute_error": float(err.mean())}
            if sel.sum() >= 5:
                row["coverage_95"] = float(np.mean(err <= 1.959963984540054 * sigs[sel]))
            by_class.append(row)
    applicability_check = {"note": "Held out error grouped by how far each held out row was from its own fold's training "
                                   "rows. Thresholds came from the training rows of that fold only. Descriptive: small "
                                   "groups say little.", "convention": app.CONVENTION_NOTE, "by_class": by_class}

    caveats = []
    if n_scored < P.RECOMMENDED_ROWS:
        caveats.append({"code": "small_sample", "message": f"Only {n_scored} held out rows were scored. Metrics from fewer than "
                                                            f"{P.RECOMMENDED_ROWS} rows are unstable."})
    if skipped:
        caveats.append({"code": "folds_skipped", "message": f"{skipped} of {len(folds)} folds were skipped for lack of training "
                                                              "input sets. Their rows are not scored."})
    if diag["n_convergence_warnings"]:
        caveats.append({"code": "convergence", "message": f"The optimiser reported convergence warnings in {diag['n_convergence_warnings']} of {diag['n_fits']} fits."})
    if diag["n_fits_with_hyperparameter_at_bound"]:
        caveats.append({"code": "hyperparameter_at_bound", "message": f"A fitted hyperparameter sat at its bound in {diag['n_fits_with_hyperparameter_at_bound']} of {diag['n_fits']} fits."})
    if key == P.RANDOM_ROWS and n > n_input_sets:
        caveats.append({"code": "replicates_in_random_split", "message": "Repeated input sets exist, so this reference result is optimistic."})
    if metrics["r2"] is None:
        caveats.append({"code": "r2_undefined", "message": "R squared is undefined because the held out targets are constant."})

    points = [{"row": int(j), "fold": int(fold_of[j]), "measured": float(y[j]), "predicted": float(pts["mu"][j]),
               "sigma": float(pts["sigma"][j]), "nearest_distance": float(pts["dist"][j]), "applicability": str(app_class[j])}
              for j in np.flatnonzero(scored)][:MAX_POINTS_STORED]
    row_ids = sorted(",".join(canonical_number(v) for v in list(X[j]) + [y[j]]) for j in np.flatnonzero(scored))
    result = _result(key, WARNINGS if caveats else COMPLETED, None, warnings=caveats, counts=counts, metrics=metrics,
                     calibration=calibration, applicability_check=applicability_check, points=points, fit_diagnostics=diag,
                     evaluation_rows_hash=_hash(row_ids), fold_assignment_hash=_hash([sorted(map(int, f)) for f in folds]))
    result["points_truncated"] = bool(int(scored.sum()) > MAX_POINTS_STORED)
    return result


def summarise(results_by_key: dict) -> dict:
    """The overall validation state, from the REQUIRED protocols only. The worst state wins, and no state means
    'approved': this describes the checks that were run, not whether the model is fit for any purpose."""
    required = [results_by_key.get(k) for k in P.REQUIRED]
    states = [r["status"] if r else "not_run" for r in required]
    if FAILED in states:
        state = "failed"
    elif any(s in ("not_run", INSUFFICIENT, NOT_AVAILABLE) for s in states):
        state = "incomplete"
    elif WARNINGS in states:
        state = "completed_with_warnings"
    else:
        state = "completed"
    return {"state": state, "required": dict(zip(P.REQUIRED, states)),
            "note": "This describes which validation protocols were run and how they ended. It is not an approval of the model."}
