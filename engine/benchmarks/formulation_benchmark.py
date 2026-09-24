"""
Reproducible computational benchmark standing in for a physical formulation lab.

Problem: optimize a 6-variable adhesive/coating formulation to MAXIMIZE bond
strength (MPa), subject to a viscosity constraint (must stay processable).

Variables (all continuous):
  x0..x3: mass fractions of Resin, Hardener, Filler, Solvent   (sum to 1.0, simplex)
  x4: cure_temp_C   in [60, 140]
  x5: cure_time_min in [10, 120]

This is NOT real customer data. It is a fixed, seeded, nonlinear response
surface with interaction terms and additive Gaussian measurement noise,
built specifically so results are reproducible and clearly labeled as
computational-benchmark results, per the brief's requirement to prove the
optimization method works before ever touching a physical lab.
"""
import numpy as np

N_COMPONENTS = 4          # resin, hardener, filler, solvent (simplex, sum=1)
PROCESS_BOUNDS = np.array([[60.0, 140.0], [10.0, 120.0]])  # cure_temp, cure_time
DIM = N_COMPONENTS + 2     # 6 total design variables
NOISE_STD = 0.35            # MPa measurement noise, mimics real lab variance

_rng_truth = np.random.default_rng(20260822)
# Fixed "true" interaction coefficients (unknown to the optimizer, known here
# only to generate ground truth) -- this stands in for the real physics/chemistry.
_A = _rng_truth.uniform(-1, 1, size=(DIM, DIM))
_A = (_A + _A.T) / 2  # symmetric interaction matrix
_b = _rng_truth.uniform(0.5, 2.0, size=DIM)
_opt_center_frac = np.array([0.42, 0.23, 0.20, 0.15])   # near-optimal component mix
_opt_center_proc = np.array([102.0, 55.0])               # near-optimal temp/time


def _normalize_fractions(frac):
    frac = np.clip(frac, 1e-6, None)
    return frac / frac.sum()


def _decode(x):
    """x is length-6 raw vector in [0,1]^6. Decode to (fractions[4], temp, time)."""
    frac = _normalize_fractions(x[:N_COMPONENTS])
    temp = PROCESS_BOUNDS[0, 0] + x[4] * (PROCESS_BOUNDS[0, 1] - PROCESS_BOUNDS[0, 0])
    time = PROCESS_BOUNDS[1, 0] + x[5] * (PROCESS_BOUNDS[1, 1] - PROCESS_BOUNDS[1, 0])
    return frac, temp, time


def true_bond_strength(x, noisy=True, rng=None):
    """Ground-truth bond strength (MPa). Higher is better. This is the
    quantity a physical pull-test would measure in a real lab."""
    frac, temp, time = _decode(x)
    proc_norm = np.array([
        (temp - _opt_center_proc[0]) / 40.0,
        (time - _opt_center_proc[1]) / 45.0,
    ])
    frac_dev = frac - _opt_center_frac
    z = np.concatenate([frac_dev, proc_norm])

    quad = -z @ _A @ z * 3.5
    linear = _b @ np.abs(z) * -0.6
    base = 18.5

    interaction_bonus = 4.0 * np.exp(-6.0 * np.sum(frac_dev ** 2)) * np.exp(-0.5 * np.sum(proc_norm ** 2))

    val = base + quad + linear + interaction_bonus
    val = max(val, 0.5)
    if noisy:
        r = rng if rng is not None else np.random.default_rng()
        val += r.normal(0, NOISE_STD)
    return float(val)


def true_viscosity(x):
    """Ground-truth process viscosity (Pa.s). Must stay <= 5.0 to be
    pumpable/processable on standard coating lines (hard constraint)."""
    frac, temp, _ = _decode(x)
    filler, solvent = frac[2], frac[3]
    visc = 2.0 + 6.0 * filler - 4.5 * solvent - 0.015 * (temp - 60)
    return float(max(visc, 0.05))


def evaluate(x, rng=None):
    """One 'computational experiment': returns (bond_strength, viscosity, feasible)."""
    y = true_bond_strength(x, noisy=True, rng=rng)
    v = true_viscosity(x)
    feasible = v <= 5.0
    return y, v, feasible


def global_optimum_reference(n_grid=200000, seed=1):
    """Dense random search under the constraint to establish a reference
    'best achievable' value for computing % of optimum reached. Uses the
    NOISELESS surface so the reference is stable/reproducible."""
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, size=(n_grid, DIM))
    best_y, best_x = -np.inf, None
    for x in X:
        v = true_viscosity(x)
        if v > 5.0:
            continue
        y = true_bond_strength(x, noisy=False)
        if y > best_y:
            best_y, best_x = y, x
    return best_y, best_x
