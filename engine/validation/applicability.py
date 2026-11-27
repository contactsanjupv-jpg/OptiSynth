"""Applicability diagnostics: how far a point is from the data a model was fitted on.

These are DIAGNOSTICS. They are not validated probabilities, not guarantees that a prediction is right inside the
flagged-clean region, and not proof that it is wrong outside it. Distances use the min-max scaling of the training
rows with every feature weighted equally, so a feature that does not affect the target counts as much as one that
does. The thresholds are working conventions taken from the spacing of the training data itself; they are never
fitted on the points being assessed.
"""
import numpy as np

from engine.preprocessing.normalization import normalize

# Working conventions, shown with every result. Not validated standards.
SPARSE_QUANTILE = 0.90
MIN_REFERENCE_POINTS = 8  # distinct training input sets needed before a spacing reference is meaningful

WITHIN = "within_training_spacing"
SPARSE = "sparse_region"
ISOLATED = "isolated"
NOT_ASSESSED = "not_assessed"
CLASSES = (WITHIN, SPARSE, ISOLATED, NOT_ASSESSED)

CONVENTION_NOTE = (
    f"Working convention: a point is in a sparse region when its nearest training input set is farther away than "
    f"{int(SPARSE_QUANTILE * 100)}% of the nearest neighbour distances between the training input sets themselves, "
    f"and isolated when it is farther than any of them. Distances are in min-max scaled inputs with equal feature "
    f"weights. These are diagnostics, not probabilities or guarantees."
)


def unique_inputs(Xn: np.ndarray) -> np.ndarray:
    """Distinct input vectors. Repeated measurements of one input set are one location, not several."""
    return np.unique(np.asarray(Xn, dtype=float), axis=0)


def reference_distances(Xn_train: np.ndarray):
    """Leave one out nearest neighbour distance for each distinct training input set, sorted ascending, or None
    when there are too few distinct sets. Uses training rows only."""
    U = unique_inputs(Xn_train)
    if len(U) < MIN_REFERENCE_POINTS:
        return None
    diff = U[:, None, :] - U[None, :, :]
    dist = np.sqrt((diff ** 2).sum(axis=2))
    np.fill_diagonal(dist, np.inf)
    return np.sort(dist.min(axis=1))


def thresholds(reference):
    if reference is None:
        return None
    return {"sparse_above": float(np.quantile(reference, SPARSE_QUANTILE)), "isolated_above": float(reference.max()),
            "reference_points": int(len(reference))}


def nearest_distance(xn: np.ndarray, Xn_train: np.ndarray):
    """(distance, index into unique_inputs(Xn_train)) of the nearest distinct training input set."""
    U = unique_inputs(Xn_train)
    d = np.sqrt(((U - np.asarray(xn, dtype=float)) ** 2).sum(axis=1))
    i = int(np.argmin(d))
    return float(d[i]), i


def classify(distance: float, limits) -> str:
    if limits is None:
        return NOT_ASSESSED
    if distance > limits["isolated_above"]:
        return ISOLATED
    if distance > limits["sparse_above"]:
        return SPARSE
    return WITHIN


def assess_points(Xn_points: np.ndarray, Xn_train: np.ndarray) -> list:
    """Assess each row of Xn_points against the training set. The reference and thresholds come from Xn_train
    only; nothing about the assessed points influences them."""
    limits = thresholds(reference_distances(Xn_train))
    out = []
    for xn in np.atleast_2d(Xn_points):
        d, idx = nearest_distance(xn, Xn_train)
        out.append({"nearest_distance": d, "nearest_index": idx, "class": classify(d, limits)})
    return out, limits


def nearest_input_set(x_raw, X_raw_train, lo, hi):
    """Nearest DISTINCT training input set to a point, found in min-max scaled inputs and returned in raw units.
    Repeated measurements of one input set are one location. Distinct sets are taken from the raw values and
    scaled afterwards, so the vector returned is always one that exists in the training data, whatever the row
    order or number of repeats. Returns (scaled distance, raw nearest input set, number of training rows at it)."""
    X = np.asarray(X_raw_train, dtype=float)
    U, counts = np.unique(X, axis=0, return_counts=True)
    span = np.where(np.asarray(hi) > np.asarray(lo), np.asarray(hi) - np.asarray(lo), 1.0)
    d = np.sqrt((((U - np.asarray(lo)) / span - (np.asarray(x_raw, dtype=float) - np.asarray(lo)) / span) ** 2).sum(axis=1))
    i = int(np.argmin(d))  # ties go to the first in sorted order, so the answer does not depend on row order
    return float(d[i]), U[i], int(counts[i])


def build_reference(X_raw_train) -> dict:
    """Everything needed to assess points against one training set: scaling bounds, the spacing reference and its
    thresholds. Built once per training set from the training rows only."""
    X = np.asarray(X_raw_train, dtype=float)
    lo, hi = X.min(axis=0), X.max(axis=0)
    return {"lo": lo, "hi": hi, "limits": thresholds(reference_distances(normalize(X, lo, hi))),
            "n_input_sets": int(len(unique_inputs(X)))}


def assess_candidate(x_raw, X_raw_train, reference: dict) -> dict:
    """The single applicability calculation used by the validation views and by candidate decision support."""
    d, vec, rows = nearest_input_set(x_raw, X_raw_train, reference["lo"], reference["hi"])
    return {"nearest_distance": d, "class": classify(d, reference["limits"]), "nearest_input": vec, "nearest_rows": rows}
