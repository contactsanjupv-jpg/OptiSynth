"""Level 2 review: (1) every GP fit receives a reproducible, fold-specific random_state derived from the
evaluation seed; (2) noise-at-lower-bound is detected for any number of inputs and never disagrees with the
at-bound counter; (3) warning capture and re-emission still work.
Run:  python3 -m unittest engine.tests.test_fit_seed_and_diagnostics -v"""
import unittest
import warnings
from unittest import mock

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

from engine.evaluation import model_metrics as mm
from engine.models.surrogate import build_surrogate


def _data(n=30, d=3, noise=1.0, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, (n, d))
    return X, 40 + 30 * X[:, 0] + rng.normal(0, noise, n)


class _Recorder:
    """Wraps build_surrogate and remembers every real GP it builds, so the random_state they END UP with can be read."""

    def __init__(self):
        self.gps = []

    def __call__(self, dim, random_state=0):
        gp = build_surrogate(dim, random_state=random_state)
        self.gps.append(gp)
        return gp

    def states(self):
        return [gp.random_state for gp in self.gps]


def _states(fn, X, y, **kw):
    rec = _Recorder()
    with mock.patch.object(mm, "build_surrogate", rec), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = fn(X, y, **kw)
    return rec.states(), result


class TestSeedReachesEveryFit(unittest.TestCase):
    def test_the_old_behaviour_is_gone_the_default_seed_is_not_used_for_every_fold(self):
        X, y = _data()
        states, result = _states(mm.compute_model_metrics, X, y)
        self.assertEqual(len(states), result["n_folds_used"])
        self.assertEqual(len(set(states)), len(states), "folds must not share one random_state")
        self.assertTrue(all(isinstance(s, int) and 0 <= s < 2 ** 32 for s in states))

    def test_same_seed_same_states_different_seed_different_states(self):
        X, y = _data()
        a, _ = _states(mm.compute_model_metrics, X, y, seed=3)
        b, _ = _states(mm.compute_model_metrics, X, y, seed=3)
        c, _ = _states(mm.compute_model_metrics, X, y, seed=4)
        self.assertEqual(a, b)
        self.assertTrue(set(a).isdisjoint(c))

    def test_metrics_and_calibration_fit_each_fold_with_the_same_state(self):
        X, y = _data()
        m, _ = _states(mm.compute_model_metrics, X, y, seed=5)
        c, _ = _states(mm.compute_uncertainty_calibration, X, y, seed=5)
        self.assertEqual(m, c)
        self.assertEqual(m, [mm._fold_seed(5, i) for i in range(len(m))])

    def test_grouped_validation_uses_fold_seeds_too(self):
        X, y = _data(n=36)
        groups = list(np.repeat(np.arange(12), 3))
        states, result = _states(mm.compute_model_metrics, X, y, seed=2, groups=groups)
        self.assertEqual(states, [mm._fold_seed(2, i) for i in range(result["n_folds_used"])])

    def test_fold_seed_properties(self):
        self.assertEqual(mm._fold_seed(0, 0), mm._fold_seed(0, 0))
        grid = {mm._fold_seed(s, f) for s in range(5) for f in range(10)}
        self.assertEqual(len(grid), 50, "no collisions between (seed, fold) pairs")
        with self.assertRaises(ValueError):
            mm._fold_seed(-1, 0)

    def test_results_are_exactly_reproducible_for_a_seed(self):
        X, y = _data()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            a = (mm.compute_model_metrics(X, y, seed=1), mm.compute_uncertainty_calibration(X, y, seed=1))
            b = (mm.compute_model_metrics(X, y, seed=1), mm.compute_uncertainty_calibration(X, y, seed=1))
        self.assertEqual(a, b)

    def test_architecture_and_bounds_are_untouched(self):
        gp = build_surrogate(3)
        self.assertEqual(gp.n_restarts_optimizer, 2)
        self.assertTrue(gp.normalize_y)
        self.assertEqual(gp.random_state, 0)  # build_surrogate's own default is unchanged
        k = gp.kernel
        self.assertEqual(k.k2.noise_level_bounds, (1e-4, 5.0))
        self.assertEqual(k.k1.k1.constant_value_bounds, (1e-2, 1e3))
        self.assertEqual(k.k1.k2.length_scale_bounds, (1e-2, 1e2))


class _KernelStandIn:
    """Stands in for a fitted GP: no optimiser, just a chosen set of fitted kernel values."""

    def __init__(self, kernel, emit=None):
        self.kernel_, self._emit = kernel, emit

    def set_params(self, **kw):
        self.params = kw

    def fit(self, X, y):
        for message, category in (self._emit or []):
            warnings.warn(message, category)
        return self


def _kernel(length_scale, noise, noise_bounds=(1e-4, 5.0)):
    return (ConstantKernel(1.0, (1e-2, 1e3)) * Matern(length_scale=np.asarray(length_scale, float),
                                                      length_scale_bounds=(1e-2, 1e2), nu=2.5)
            + WhiteKernel(noise_level=noise, noise_level_bounds=noise_bounds))


def _record(kernel, emit=None):
    diag = mm._new_fit_diagnostics()
    with mock.patch.object(mm, "build_surrogate", lambda d: _KernelStandIn(kernel, emit)):
        mm._fit_recorded(3, np.zeros((4, 3)), np.zeros(4), diag)
    return diag


class TestNoiseAtLowerBound(unittest.TestCase):
    def test_noise_only_at_its_floor_is_counted_with_a_multi_element_length_scale(self):
        d = _record(_kernel([1.0, 1.0, 1.0], 1e-4))
        self.assertEqual((d["n_fits_noise_at_lower_bound"], d["n_fits_with_hyperparameter_at_bound"],
                          d["n_convergence_warnings"]), (1, 1, 0))

    def test_a_length_scale_at_its_floor_is_not_mistaken_for_noise(self):
        """Regression for indexing hyperparameters instead of theta entries: element 3 is the noise term only when
        the length_scale has ONE element; here it has three."""
        d = _record(_kernel([1e-2, 1.0, 1.0], 0.1))
        self.assertEqual((d["n_fits_noise_at_lower_bound"], d["n_fits_with_hyperparameter_at_bound"]), (0, 1))
        d = _record(_kernel([1.0, 1.0, 1e-2], 0.1))
        self.assertEqual((d["n_fits_noise_at_lower_bound"], d["n_fits_with_hyperparameter_at_bound"]), (0, 1))

    def test_noise_at_its_ceiling_or_interior_is_not_a_floor_hit(self):
        d = _record(_kernel([1.0, 1.0, 1.0], 5.0))
        self.assertEqual((d["n_fits_noise_at_lower_bound"], d["n_fits_with_hyperparameter_at_bound"]), (0, 1))
        d = _record(_kernel([1.0, 1.0, 1.0], 0.1))
        self.assertEqual((d["n_fits_noise_at_lower_bound"], d["n_fits_with_hyperparameter_at_bound"]), (0, 0))

    def test_the_two_counters_never_disagree_near_the_floor(self):
        for noise in (1e-4, 1.0005e-4, 1.005e-4, 1.02e-4, 1e-3, 0.1):
            d = _record(_kernel([1.0, 1.0, 1.0], noise))
            self.assertLessEqual(d["n_fits_noise_at_lower_bound"], d["n_fits_with_hyperparameter_at_bound"], noise)

    def test_a_fixed_term_does_not_shift_the_mapping(self):
        k = _kernel([1.0, 1.0, 1.0], 1e-4, noise_bounds=(1e-4, 5.0))
        k.k1.k1.constant_value_bounds = "fixed"
        d = _record(k)
        self.assertEqual(d["n_fits_noise_at_lower_bound"], 1)

    def test_real_fits_noise_free_data_for_one_two_and_four_inputs(self):
        for dim in (1, 2, 4):
            rng = np.random.default_rng(dim)
            X = rng.uniform(0, 1, (30, dim))
            y = 10 + 5 * X[:, 0]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                diag = mm.compute_model_metrics(X, y)["fit_diagnostics"]
            self.assertGreaterEqual(diag["n_fits_noise_at_lower_bound"], 1, dim)
            self.assertGreaterEqual(diag["n_fits_with_hyperparameter_at_bound"], diag["n_fits_noise_at_lower_bound"], dim)

    def test_noisy_data_does_not_report_the_noise_floor(self):
        X, y = _data(n=40, d=2, noise=6.0, seed=3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            diag = mm.compute_model_metrics(X, y)["fit_diagnostics"]
        self.assertEqual(diag["n_fits_noise_at_lower_bound"], 0)


class TestWarningCaptureAndReemission(unittest.TestCase):
    EMIT = [("lbfgs did not converge", ConvergenceWarning), ("something else", UserWarning),
            ("another convergence", ConvergenceWarning)]

    def _run(self, emit):
        diag = mm._new_fit_diagnostics()
        with mock.patch.object(mm, "build_surrogate", lambda d: _KernelStandIn(_kernel([1.0] * 3, 0.1), emit)):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                mm._fit_recorded(3, np.zeros((4, 3)), np.zeros(4), diag)
        return diag, caught

    def test_every_warning_is_reemitted_with_its_category_and_message(self):
        diag, caught = self._run(self.EMIT)
        self.assertEqual([(str(w.message), w.category) for w in caught], [(m, c) for m, c in self.EMIT])

    def test_only_convergence_warnings_are_counted_and_once_per_fit(self):
        diag, _ = self._run(self.EMIT)
        self.assertEqual((diag["n_fits"], diag["n_convergence_warnings"]), (1, 1))
        diag, _ = self._run([("note", UserWarning)])
        self.assertEqual(diag["n_convergence_warnings"], 0)
        diag, caught = self._run(None)
        self.assertEqual((diag["n_fits"], len(caught)), (1, 0))

    def test_callers_filters_are_restored(self):
        before = list(warnings.filters)
        self._run(self.EMIT)
        self.assertEqual(warnings.filters, before)

    def test_counters_are_correct_even_if_the_caller_turns_warnings_into_errors(self):
        diag = mm._new_fit_diagnostics()
        emit = [("lbfgs did not converge", ConvergenceWarning)]
        with mock.patch.object(mm, "build_surrogate", lambda d: _KernelStandIn(_kernel([1.0] * 3, 0.1), emit)):
            with warnings.catch_warnings():
                warnings.simplefilter("error")
                with self.assertRaises(ConvergenceWarning):
                    mm._fit_recorded(3, np.zeros((4, 3)), np.zeros(4), diag)
        self.assertEqual((diag["n_fits"], diag["n_convergence_warnings"]), (1, 1))

    def test_an_exception_in_fit_propagates_and_filters_are_restored(self):
        class Boom(_KernelStandIn):
            def fit(self, X, y):
                raise RuntimeError("fit failed")

        before = list(warnings.filters)
        with mock.patch.object(mm, "build_surrogate", lambda d: Boom(_kernel([1.0] * 3, 0.1))):
            with self.assertRaises(RuntimeError):
                mm._fit_recorded(3, np.zeros((4, 3)), np.zeros(4), mm._new_fit_diagnostics())
        self.assertEqual(warnings.filters, before)

    def test_a_real_gp_warning_reaches_the_caller_end_to_end(self):
        rng = np.random.default_rng(0)
        X = rng.uniform(0, 1, (30, 2))
        y = 10 + 5 * X[:, 0]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            diag = mm.compute_model_metrics(X, y)["fit_diagnostics"]
        seen = sum(issubclass(w.category, ConvergenceWarning) for w in caught)
        self.assertGreaterEqual(seen, diag["n_convergence_warnings"])
        self.assertGreater(diag["n_convergence_warnings"], 0)


if __name__ == "__main__":
    unittest.main()
