"""
Surrogate model construction. This is the ONLY place a Gaussian Process is
instantiated -- if the kernel or regularization needs tuning, it changes
here and nowhere else.
"""
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel


def build_surrogate(dim: int, random_state: int = 0) -> GaussianProcessRegressor:
    """Returns an unfit GP regressor configured for a `dim`-dimensional,
    normalized ([0,1]^dim) input space. Matern(nu=2.5) is a reasonable
    default for physical/chemical response surfaces (twice differentiable,
    doesn't over-smooth sharp optima the way the RBF kernel can).

    random_state is REQUIRED to be set explicitly (not left as sklearn's
    default None) -- with n_restarts_optimizer > 0, scikit-learn draws its
    hyperparameter-optimization restarts from NumPy's global random state
    when random_state=None, which is NOT controlled by any seed this
    codebase passes elsewhere. Every caller passes its own `seed` through
    to this parameter so results are genuinely reproducible."""
    kernel = ConstantKernel(1.0, (1e-2, 1e3)) * Matern(
        length_scale=np.ones(dim), length_scale_bounds=(1e-2, 1e2), nu=2.5
    ) + WhiteKernel(noise_level=0.1, noise_level_bounds=(1e-4, 5.0))
    return GaussianProcessRegressor(
        kernel=kernel, normalize_y=True, n_restarts_optimizer=2, random_state=random_state
    )