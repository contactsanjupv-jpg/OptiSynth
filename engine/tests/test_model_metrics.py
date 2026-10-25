"""
Regression tests for the cross-validated model-quality metrics (engine/evaluation/model_metrics.py).

Two confirmed defects:

1. LEAKAGE. compute_model_metrics() and compute_uncertainty_calibration() fitted the feature-scaling bounds on
   ALL rows, then scaled each fold's held-out rows with them -- so the held-out rows influenced how the model's
   inputs were scaled. Bounds must come from each fold's TRAINING rows only.
2. WRONG RANK CORRELATION. The Spearman correlation used `argsort().argsort()`, which gives tied values
   distinct, arbitrary ranks. A correct Spearman uses the average rank for ties, and must say "undefined"
   (None) for constant or degenerate input instead of inventing a number.

The 10%-of-target-range accuracy band is kept, but is labelled a heuristic -- not an engineering acceptance
threshold.

All datasets are fixed synthetic arrays with fixed seeds; these tests prove the calculations are correct and
deterministic, not that the model is valid on real data.

Run:  python3 -m unittest engine.tests.test_model_metrics -v
"""
import unittest
from unittest import mock

import numpy as np
from scipy.stats import spearmanr

import engine.evaluation.model_metrics as mm
from engine.evaluation.model_metrics import (
    ACCURACY_TOLERANCE_FRACTION, _average_ranks, compute_model_metrics, compute_uncertainty_calibration,
    spearman_correlation,
)
from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize


def _smooth(n=30, d=2, seed=3):
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 10, size=(n, d))
    y = -np.sum((X - 5) ** 2, axis=1) + rng.normal(0, 0.5, n)
    return X, y


def _with_outlier(seed=11):
    """30 rows in [0, 1]^2 plus ONE row whose first feature is 50x larger. Scaling by bounds fitted on all rows
    squeezes the other 29 rows into [0, 0.02]; fitted per fold, the folds that do not hold the outlier do not."""
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 1, size=(30, 2))
    X[7, 0] = 50.0
    y = 3.0 * X[:, 1] + np.sin(4.0 * np.minimum(X[:, 0], 1.0)) + rng.normal(0, 0.05, 30)
    return X, y


def _folds(n, k, seed):
    """The fold assignment the module uses (same shuffle, same split), reconstructed independently."""
    k = max(2, min(k, n // 2, 10))
    idx = np.arange(n)
    np.random.default_rng(seed).shuffle(idx)
    return np.array_split(idx, k)


def _reference_cv(X, y, k, seed, per_fold_bounds, want_sigma=False):
    """An independent re-implementation of the cross-validation loop, used as an oracle."""
    n, d = X.shape
    folds = _folds(n, k, seed)
    k = len(folds)
    preds, sigmas = np.full(n, np.nan), np.full(n, np.nan)
    global_lo, global_hi = feature_bounds(X)
    for i in range(k):
        test = folds[i]
        train = np.concatenate([folds[j] for j in range(k) if j != i])
        lo, hi = feature_bounds(X[train]) if per_fold_bounds else (global_lo, global_hi)
        gp = build_surrogate(d).fit(normalize(X[train], lo, hi), y[train])
        if want_sigma:
            preds[test], sigmas[test] = gp.predict(normalize(X[test], lo, hi), return_std=True)
        else:
            preds[test] = gp.predict(normalize(X[test], lo, hi))
    return preds, sigmas


def _reference_metrics(X, y, k, seed, per_fold_bounds):
    preds, _ = _reference_cv(X, y, k, seed, per_fold_bounds)
    ss_res = np.sum((y - preds) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return 1 - ss_res / ss_tot, float(np.mean(np.abs(y - preds)))


class TestSpearmanCorrelation(unittest.TestCase):
    def test_hand_computed_value_with_ties(self):
        """a has ranks [2, 2, 2, 4.5, 4.5, 6], b has [1..6]: rho = 15 / sqrt(15 * 35/2) exactly."""
        a, b = [1, 1, 1, 2, 2, 3], [1, 2, 3, 4, 5, 6]
        self.assertEqual(_average_ranks(a).tolist(), [2.0, 2.0, 2.0, 4.5, 4.5, 6.0])
        self.assertAlmostEqual(spearman_correlation(a, b), 15 / np.sqrt(15 * 17.5), places=14)
        self.assertAlmostEqual(spearman_correlation(a, b), 0.9258200997725515, places=14)

    def test_the_old_argsort_approximation_got_ties_wrong(self):
        """argsort().argsort() ranks tied values by position, so a perfectly tied pair looks perfectly ordered."""
        a, b = np.array([1, 1, 1, 2, 2, 3.0]), np.arange(1.0, 7.0)

        def old(x, y):
            rx = x.argsort(kind="stable").argsort().astype(float)
            ry = y.argsort(kind="stable").argsort().astype(float)
            return float(np.corrcoef(rx, ry)[0, 1])

        self.assertAlmostEqual(old(a, b), 1.0, places=12)               # the defect: ties treated as distinct
        self.assertLess(spearman_correlation(a, b), 0.93)               # the correct value
        self.assertNotAlmostEqual(old(a, b), spearman_correlation(a, b), places=3)

    def test_matches_scipy_on_fixed_datasets_with_and_without_ties(self):
        rng = np.random.default_rng(0)
        cases = [
            (rng.normal(size=25), rng.normal(size=25)),                       # no ties
            (rng.integers(0, 4, size=40).astype(float), rng.normal(size=40)),   # heavy ties in a
            (rng.integers(0, 3, size=40).astype(float), rng.integers(0, 3, size=40).astype(float)),  # ties in both
            (np.array([5.0, 5.0, 1.0, 1.0, 3.0, 3.0]), np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])),
            (np.array([1.0, 2.0, 3.0, 4.0]), np.array([4.0, 3.0, 2.0, 1.0])),   # perfect negative
        ]
        for a, b in cases:
            self.assertAlmostEqual(spearman_correlation(a, b), float(spearmanr(a, b)[0]), places=12)

    def test_symmetric_bounded_and_invariant_to_monotone_transforms(self):
        rng = np.random.default_rng(1)
        a, b = rng.integers(0, 5, size=30).astype(float), rng.normal(size=30)
        self.assertAlmostEqual(spearman_correlation(a, b), spearman_correlation(b, a), places=14)
        self.assertAlmostEqual(spearman_correlation(a, b), spearman_correlation(np.exp(a), b ** 3), places=14)
        self.assertTrue(-1.0 <= spearman_correlation(a, b) <= 1.0)
        self.assertEqual(spearman_correlation([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertEqual(spearman_correlation([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_undefined_cases_return_none_not_a_made_up_number(self):
        self.assertIsNone(spearman_correlation([3, 3, 3, 3], [1, 2, 3, 4]))           # constant first input
        self.assertIsNone(spearman_correlation([1, 2, 3, 4], [7, 7, 7, 7]))           # constant second input
        self.assertIsNone(spearman_correlation([2, 2], [2, 2]))                       # both constant
        self.assertIsNone(spearman_correlation([1.0], [2.0]))                         # one pair
        self.assertIsNone(spearman_correlation([], []))                               # none
        self.assertIsNone(spearman_correlation([1, 2, np.nan], [1, 2, 3]))            # non-finite
        self.assertIsNone(spearman_correlation([1, 2, 3], [1, np.inf, 3]))
        with self.assertRaises(ValueError):
            spearman_correlation([1, 2, 3], [1, 2])

    def test_float_noise_is_not_mistaken_for_ties(self):
        self.assertIsNotNone(spearman_correlation([1.0, 1.0 + 1e-12, 2.0], [1, 2, 3]))
        self.assertEqual(_average_ranks([1.0, 1.0 + 1e-12, 2.0]).tolist(), [1.0, 2.0, 3.0])


class TestFeatureBoundsAreFittedPerFold(unittest.TestCase):
    def _recorded_bound_calls(self, function, X, y, k=5, seed=0):
        calls = []
        original = mm.feature_bounds

        def spy(arr):
            calls.append(np.array(arr, copy=True))
            return original(arr)

        with mock.patch.object(mm, "feature_bounds", spy):
            function(X, y, k=k, seed=seed)
        return calls

    def _assert_bounds_use_training_rows_only(self, function):
        X, y = _with_outlier()
        k, seed = 5, 0
        folds = _folds(len(X), k, seed)
        calls = self._recorded_bound_calls(function, X, y, k, seed)
        self.assertEqual(len(calls), len(folds), "one bounds fit per fold, none on the full data set")
        for i, recorded in enumerate(calls):
            train = np.concatenate([folds[j] for j in range(len(folds)) if j != i])
            np.testing.assert_array_equal(recorded, X[train], err_msg=f"fold {i}")
            for held_out_row in X[folds[i]]:
                self.assertFalse((recorded == held_out_row).all(axis=1).any(),
                                 f"fold {i}: a held-out row reached the bounds fit")

    def test_model_metrics_fit_bounds_on_each_folds_training_rows_only(self):
        self._assert_bounds_use_training_rows_only(compute_model_metrics)

    def test_uncertainty_calibration_fits_bounds_on_each_folds_training_rows_only(self):
        self._assert_bounds_use_training_rows_only(compute_uncertainty_calibration)

    def test_model_metrics_equal_an_independent_per_fold_reference(self):
        X, y = _with_outlier()
        r2, mae = _reference_metrics(X, y, k=5, seed=0, per_fold_bounds=True)
        got = compute_model_metrics(X, y, k=5, seed=0)
        self.assertAlmostEqual(got["r2_score"], round(float(r2), 3), places=9)
        self.assertAlmostEqual(got["mean_absolute_error"], round(mae, 3), places=9)

    def test_calibration_equals_an_independent_per_fold_reference(self):
        X, y = _with_outlier()
        preds, sigmas = _reference_cv(X, y, 5, 0, per_fold_bounds=True, want_sigma=True)
        errors = np.abs(y - preds)
        got = compute_uncertainty_calibration(X, y, k=5, seed=0)
        self.assertEqual(got["within_1sigma_pct"], round(float(np.mean(errors <= sigmas)) * 100, 1))
        self.assertEqual(got["within_2sigma_pct"], round(float(np.mean(errors <= 2 * sigmas)) * 100, 1))
        self.assertAlmostEqual(got["sigma_error_correlation"], round(float(spearmanr(sigmas, errors)[0]), 3), places=9)
        self.assertTrue(got["sigma_error_correlation_defined"])


class TestCalibrationCorrelationHandling(unittest.TestCase):
    class _StubModel:
        """Deterministic stand-in surrogate: predicts the training mean with a fixed sigma pattern."""

        def __init__(self, sigma_of):
            self._sigma_of = sigma_of

        def fit(self, X, y):
            self._mean = float(np.mean(y))
            return self

        def predict(self, X, return_std=False):
            mu = np.full(len(X), self._mean)
            return (mu, self._sigma_of(X)) if return_std else mu

    def test_constant_sigma_is_reported_as_undefined_not_as_zero_correlation(self):
        X, y = _smooth()
        with mock.patch.object(mm, "build_surrogate", lambda d: self._StubModel(lambda Xn: np.full(len(Xn), 2.0))):
            result = compute_uncertainty_calibration(X, y, k=5, seed=0)
        self.assertNotIn("error", result)
        self.assertFalse(result["sigma_error_correlation_defined"])
        self.assertEqual(result["sigma_error_correlation"], 0.0)  # compatibility value; the flag says it means "undefined"
        self.assertEqual(result["n_points_scored"], 30)

    def test_tied_sigma_values_use_average_ranks(self):
        """sigma takes only two distinct values (heavy ties); the reported correlation must be the tie-aware one."""
        X, y = _smooth()
        captured = {}
        real = mm.spearman_correlation

        def spy(a, b):
            captured["args"] = (np.array(a, copy=True), np.array(b, copy=True))
            return real(a, b)

        stub = lambda d: self._StubModel(lambda Xn: np.where(Xn[:, 0] > 0.5, 3.0, 1.0))
        with mock.patch.object(mm, "build_surrogate", stub), mock.patch.object(mm, "spearman_correlation", spy):
            result = compute_uncertainty_calibration(X, y, k=5, seed=0)
        sigma, errors = captured["args"]
        self.assertLessEqual(len(set(sigma.tolist())), 2)
        self.assertTrue(result["sigma_error_correlation_defined"])
        self.assertAlmostEqual(result["sigma_error_correlation"], round(float(spearmanr(sigma, errors)[0]), 3), places=9)


class TestAccuracyToleranceIsLabelledAHeuristic(unittest.TestCase):
    def test_band_is_ten_percent_of_the_target_range_and_says_it_is_a_heuristic(self):
        X, y = _smooth()
        result = compute_model_metrics(X, y, k=5, seed=0)
        self.assertEqual(ACCURACY_TOLERANCE_FRACTION, 0.10)
        self.assertEqual(result["accuracy_tolerance_band"], round(0.10 * float(y.max() - y.min()), 3))
        note = result["accuracy_tolerance_note"]
        self.assertIn("Heuristic", note)
        self.assertIn("not an engineering acceptance threshold", note)
        for kept in ("r2_score", "mean_absolute_error", "prediction_accuracy_pct", "accuracy_tolerance_band",
                     "n_folds_used", "n_points_scored"):
            self.assertIn(kept, result, "existing result keys are preserved")


class TestDeterminismOnFixedDatasets(unittest.TestCase):
    def test_identical_results_on_repeated_runs(self):
        for X, y in (_smooth(), _with_outlier()):
            self.assertEqual(compute_model_metrics(X, y, k=5, seed=0), compute_model_metrics(X, y, k=5, seed=0))
            self.assertEqual(compute_uncertainty_calibration(X, y, k=5, seed=0),
                             compute_uncertainty_calibration(X, y, k=5, seed=0))

    def test_degenerate_inputs_still_return_the_documented_errors(self):
        X, y = _smooth(n=4)
        self.assertIn("error", compute_model_metrics(X, y))
        self.assertIn("error", compute_uncertainty_calibration(X, y))


if __name__ == "__main__":
    unittest.main()
