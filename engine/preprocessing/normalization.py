"""
Feature scaling. GPs are fit in normalized [0,1]^d space for numerical
stability across features with very different physical units (e.g. a
0-1 mass fraction alongside a 60-140 degC temperature).
"""
import numpy as np


def feature_bounds(X: np.ndarray):
    """Returns (lo, hi) per-column bounds from observed data."""
    return X.min(axis=0), X.max(axis=0)


def normalize(X: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    span = np.where(hi > lo, hi - lo, 1.0)
    return (X - lo) / span


def denormalize(Xn: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return Xn * (hi - lo) + lo
