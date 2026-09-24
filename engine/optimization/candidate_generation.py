"""
Candidate generation: produces the pool of not-yet-evaluated points the
acquisition function scores. Latin Hypercube sampling is used because it
spreads points more evenly across the design space than uniform random
sampling for the same sample count, which matters when the candidate pool
is only a few thousand points in a 5-10 dimensional space.
"""
import numpy as np
from scipy.stats import qmc


def generate_candidate_pool(dim: int, n_candidates: int, seed: int) -> np.ndarray:
    """Returns an (n_candidates, dim) array in normalized [0,1]^dim space."""
    sampler = qmc.LatinHypercube(d=dim, seed=seed)
    return sampler.random(n=n_candidates)
