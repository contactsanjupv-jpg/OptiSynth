"""
Acquisition functions. An acquisition function scores how valuable it would
be to run a candidate experiment next, given the surrogate's current belief
(mean + uncertainty). This module only knows about mu/sigma arrays -- it has
no idea what a "constraint" or "candidate pool" is; see engine/constraints
and engine/optimization for those.
"""
import numpy as np
from scipy.stats import norm


def expected_improvement(mu: np.ndarray, sigma: np.ndarray, best_so_far: float,
                          direction: str, xi: float = 0.01) -> np.ndarray:
    """
    Classic Expected Improvement (Mockus, 1978). `xi` is a small exploration
    margin -- without it, EI can collapse to near-zero once the surrogate is
    confident, causing the optimizer to stop exploring prematurely.
    """
    sigma = np.maximum(sigma, 1e-9)
    if direction == "minimize":
        improvement = best_so_far - mu - xi
    elif direction == "maximize":
        improvement = mu - best_so_far - xi
    else:
        raise ValueError(f"direction must be 'minimize' or 'maximize', got {direction!r}")

    z = improvement / sigma
    ei = improvement * norm.cdf(z) + sigma * norm.pdf(z)
    return np.maximum(ei, 0.0)
