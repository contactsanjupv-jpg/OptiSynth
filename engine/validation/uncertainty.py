"""What the fitted model says about its own predictive spread, kept apart from the other kinds of uncertainty.

The Gaussian process reports one predictive standard deviation. In scikit-learn that number already contains the
fitted white noise term, so it is the sum (in variance) of two things: uncertainty about the underlying function at
that point, and the scatter the model could not explain in the training data. Both are properties of THIS MODEL
fitted to THIS DATA. Neither is a measurement uncertainty: the noise term absorbs measurement error, batch to batch
variation, missing inputs and model misfit together, and cannot say which."""
import warnings

import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize

TYPES = {
    "model": "Spread of the model's prediction about the underlying function, plus the scatter it could not explain. "
             "Taken from the fitted Gaussian process.",
    "measurement": "Uncertainty of the measurement method itself (instrument, method precision, laboratory). Not "
                   "recorded for this evidence, so it is not assessed.",
    "data": "How much evidence there is and how it is repeated: row counts, distinct input sets, repeated input sets "
            "and the target range. Describes the evidence, not a prediction interval.",
    "applicability": "Whether a candidate sits where the evidence has data: range checks and distance from the nearest "
                     "training input set. A diagnostic, not a probability.",
}


def fitted_noise_summary(X, y, seed: int = 0) -> dict:
    """Fits the surrogate exactly as ranking does and reads back the noise term. Returns a dict with
    status 'ok' or 'failed'."""
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    try:
        lo, hi = feature_bounds(X)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            gp = build_surrogate(X.shape[1], random_state=seed).fit(normalize(X, lo, hi), y)
        kernel = gp.kernel_
        noise_level = next((float(v) for k, v in kernel.get_params().items() if k.endswith("noise_level")), None)
        y_std = float(getattr(gp, "_y_train_std", np.std(y)) or 1.0)
        noise_sd = float(np.sqrt(noise_level) * y_std) if noise_level is not None else None
        return {"status": "ok", "fitted_kernel": str(kernel), "fitted_noise_sd": noise_sd, "target_sd": float(np.std(y, ddof=1)),
                "note": "Fitted noise is the scatter the model left unexplained. It is not a measurement uncertainty."}
    except Exception as exc:
        return {"status": "failed", "reason": f"The model could not be fitted ({type(exc).__name__}).", "fitted_noise_sd": None}


def data_summary(X, y, feature_columns, groups) -> dict:
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    sizes = {}
    for g in groups:
        sizes[g] = sizes.get(g, 0) + 1
    repeated = [g for g, c in sizes.items() if c > 1]
    return {
        "n_rows": int(len(y)),
        "n_input_sets": int(len(sizes)),
        "n_repeated_input_sets": len(repeated),
        "rows_in_repeated_input_sets": int(sum(sizes[g] for g in repeated)),
        "feature_ranges": {c: [float(X[:, i].min()), float(X[:, i].max())] for i, c in enumerate(feature_columns)},
        "target_range": [float(y.min()), float(y.max())],
        "target_sd": float(np.std(y, ddof=1)) if len(y) > 1 else None,
    }


def replicate_scatter(y, groups) -> dict:
    """Observed spread between repeated rows of one input set. It mixes measurement error with sample, batch and
    operator differences, so it is reported as scatter and never as a measurement uncertainty."""
    by = {}
    for v, g in zip(np.asarray(y, dtype=float), groups):
        by.setdefault(g, []).append(v)
    sds = [float(np.std(v, ddof=1)) for v in by.values() if len(v) > 1]
    if not sds:
        return {"status": "no_repeated_input_sets", "n_sets": 0, "pooled_sd": None,
                "note": "No input set was measured more than once, so repeated scatter cannot be observed."}
    dof = sum(len(v) - 1 for v in by.values() if len(v) > 1)
    pooled = float(np.sqrt(sum((len(v) - 1) * np.var(v, ddof=1) for v in by.values() if len(v) > 1) / dof))
    return {"status": "observed", "n_sets": len(sds), "degrees_of_freedom": int(dof), "pooled_sd": pooled,
            "note": "Observed spread between repeated rows. It mixes measurement error with sample, batch and operator "
                    "differences and is not a measurement uncertainty."}
