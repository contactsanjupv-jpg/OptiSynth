"""Group-aware cross-validation (Level 2). Synthetic data is used ONLY to show the mechanism (replicate leakage);
it says nothing about accuracy on any real material.
Run:  python3 -m unittest engine.tests.test_grouped_validation -v"""
import unittest
import warnings
from unittest import mock

import numpy as np
from sklearn.exceptions import ConvergenceWarning

from engine.evaluation import model_metrics as mm
from engine.evaluation.model_metrics import compute_model_metrics, compute_uncertainty_calibration


def _replicated(n_groups=20, reps=5, noise=0.5, freq=10, seed=0):
    rng = np.random.default_rng(seed)
    U = rng.uniform(0, 1, (n_groups, 3))
    X = np.repeat(U, reps, axis=0)
    y = 40 + 30 * X[:, 0] + 10 * np.sin(freq * X[:, 2]) + 8 * np.sin(freq * X[:, 1]) + rng.normal(0, noise, len(X))
    return X, y, np.repeat(np.arange(n_groups), reps)


class TestMakeFolds(unittest.TestCase):
    def test_no_group_is_split_and_every_row_is_used_once(self):
        groups = list(np.repeat(np.arange(12), 3))
        folds = mm._make_folds(36, 4, 0, groups)
        self.assertEqual(sorted(np.concatenate(folds).tolist()), list(range(36)))
        seen = {}
        for i, f in enumerate(folds):
            for r in f:
                seen.setdefault(groups[r], set()).add(i)
        self.assertTrue(all(len(v) == 1 for v in seen.values()))

    def test_deterministic_and_seed_dependent(self):
        groups = list(np.repeat(np.arange(12), 3))
        a, b, c = (mm._make_folds(36, 4, s, groups) for s in (0, 0, 1))
        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(a, b)))
        self.assertFalse(all(np.array_equal(x, y) for x, y in zip(a, c)))

    def test_without_groups_behaviour_is_the_original_random_row_split(self):
        rng = np.random.default_rng(3)
        idx = np.arange(25)
        rng.shuffle(idx)
        expected = np.array_split(idx, 5)
        got = mm._make_folds(25, 5, 3, None)
        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(expected, got)))

    def test_tuple_and_string_labels_are_accepted(self):
        folds = mm._make_folds(6, 2, 0, [("a", 1), ("a", 1), "b", "b", "c", "c"])
        self.assertEqual(sorted(np.concatenate(folds).tolist()), list(range(6)))


def _groups_from_sizes(sizes):
    return [g for g, n in enumerate(sizes) for _ in range(n)]


def _round_robin_by_group_count(n_groups, sizes, k, seed):
    """The ORIGINAL assignment (balances the number of groups, not rows), kept only to prove the improvement."""
    rng = np.random.default_rng(seed)
    loads = [0] * k
    for pos, g in enumerate(rng.permutation(n_groups)):
        loads[pos % k] += sizes[g]
    return loads


class TestFoldBalanceWithUnequalGroups(unittest.TestCase):
    UNEQUAL = [24, 12, 6, 6, 3, 3, 2, 2, 1, 1, 1, 1]  # 62 rows, 12 input sets
    ACHIEVABLE = [8, 7, 6, 5, 4, 3, 3, 2, 2, 1, 1, 1]  # 43 rows; no group larger than an average fold

    def _folds(self, sizes, k, seed):
        groups = _groups_from_sizes(sizes)
        return groups, mm._make_folds(len(groups), k, seed, groups)

    def test_no_group_crosses_folds_and_every_row_is_used_once(self):
        for sizes in (self.UNEQUAL, self.ACHIEVABLE, [30] + [1] * 9):
            for seed in range(5):
                groups, folds = self._folds(sizes, 5, seed)
                self.assertEqual(sorted(np.concatenate(folds).tolist()), list(range(len(groups))))
                where = {}
                for i, f in enumerate(folds):
                    for r in f:
                        where.setdefault(groups[r], set()).add(i)
                self.assertTrue(all(len(v) == 1 for v in where.values()), (sizes, seed))
                for g, n in enumerate(sizes):  # a group's rows all sit together
                    self.assertEqual(sum(1 for r in range(len(groups)) if groups[r] == g), n)

    def test_fold_row_counts_are_balanced_when_balance_is_possible(self):
        for seed in range(5):
            _, folds = self._folds(self.ACHIEVABLE, 5, seed)
            counts = [len(f) for f in folds]
            self.assertLessEqual(max(counts) - min(counts), 1, (seed, counts))

    def test_unequal_groups_are_far_better_balanced_than_round_robin(self):
        sizes = self.UNEQUAL
        for seed in range(6):
            _, folds = self._folds(sizes, 5, seed)
            counts = [len(f) for f in folds]
            old = _round_robin_by_group_count(len(sizes), sizes, 5, seed)
            self.assertEqual(sum(counts), sum(sizes))
            self.assertEqual(sorted(counts), [8, 9, 9, 12, 24])  # the largest group (24 rows) fills one fold alone
            self.assertLess(max(counts) - min(counts), max(old) - min(old), (seed, counts, old))
            self.assertLessEqual(max(counts) - min(counts), 16)

    def test_a_group_bigger_than_an_average_fold_is_not_split_and_no_fold_is_empty(self):
        for seed in range(5):
            groups, folds = self._folds([30] + [1] * 9, 5, seed)
            counts = sorted(len(f) for f in folds)
            self.assertEqual(counts[-1], 30)
            self.assertTrue(all(c >= 2 for c in counts))

    def test_deterministic_for_a_seed_and_seed_still_changes_equal_sized_choices(self):
        sizes = [1] * 12 + [4, 4, 4]
        a, b = (self._folds(sizes, 5, 0)[1] for _ in range(2))
        self.assertTrue(all(np.array_equal(x, y) for x, y in zip(a, b)))
        c = self._folds(sizes, 5, 1)[1]
        self.assertFalse(all(np.array_equal(x, y) for x, y in zip(a, c)))
        self.assertEqual(sorted(len(f) for f in a), sorted(len(f) for f in c))  # same balance, different partition

    def test_equal_sized_groups_stay_perfectly_balanced(self):
        _, folds = self._folds([3] * 10, 5, 0)
        self.assertEqual([len(f) for f in folds], [6] * 5)

    def test_metrics_run_on_unequal_replicate_counts(self):
        rng = np.random.default_rng(0)
        sizes = [12, 6, 4, 3, 3, 2, 2, 1, 1, 1]
        U = rng.uniform(0, 1, (len(sizes), 3))
        X = np.repeat(U, sizes, axis=0)
        y = 40 + 30 * X[:, 0] + 5 * X[:, 1] + rng.normal(0, 0.5, len(X))
        groups = _groups_from_sizes(sizes)
        m = compute_model_metrics(X, y, groups=groups)
        self.assertNotIn("error", m)
        self.assertEqual(m["n_points_scored"], len(y))
        self.assertEqual(m["n_groups"], len(sizes))
        self.assertEqual(compute_uncertainty_calibration(X, y, groups=groups)["n_points_scored"], len(y))


class TestGroupedMetrics(unittest.TestCase):
    def test_replicate_leakage_inflates_random_row_r2(self):
        X, y, g = _replicated()
        random_rows = compute_model_metrics(X, y)
        grouped = compute_model_metrics(X, y, groups=list(g))
        self.assertEqual(random_rows["cv_scheme"], "random_rows")
        self.assertIsNone(random_rows["n_groups"])
        self.assertEqual(grouped["cv_scheme"], "grouped")
        self.assertEqual(grouped["n_groups"], 20)
        self.assertGreater(random_rows["r2_score"], 0.95)
        self.assertGreater(random_rows["r2_score"] - grouped["r2_score"], 0.3)

    def test_calibration_reports_its_scheme_and_scores_all_points(self):
        X, y, g = _replicated(n_groups=15, reps=3)
        cal = compute_uncertainty_calibration(X, y, groups=list(g))
        self.assertNotIn("error", cal)
        self.assertEqual(cal["cv_scheme"], "grouped")
        self.assertEqual(cal["n_points_scored"], len(y))
        self.assertEqual(compute_uncertainty_calibration(X, y)["cv_scheme"], "random_rows")

    def test_boundary_exactly_six_groups_works_five_is_refused(self):
        X, y, g = _replicated(n_groups=6, reps=3, noise=1.0, freq=3)
        self.assertNotIn("error", compute_model_metrics(X, y, groups=list(g)))
        X5, y5, g5 = _replicated(n_groups=5, reps=4)
        for fn in (compute_model_metrics, compute_uncertainty_calibration):
            result = fn(X5, y5, groups=list(g5))
            self.assertIn("error", result)
            self.assertIn("distinct input sets", result["error"])
            self.assertIn("(found 5)", result["error"])

    def test_wrong_length_groups_raise(self):
        X, y, g = _replicated(n_groups=8, reps=2)
        for fn in (compute_model_metrics, compute_uncertainty_calibration):
            with self.assertRaises(ValueError):
                fn(X, y, groups=list(g)[:-1])

    def test_no_groups_still_works_and_old_error_for_tiny_data_is_unchanged(self):
        X, y, _ = _replicated(n_groups=3, reps=1)
        self.assertEqual(compute_model_metrics(X, y)["error"],
                         "Need at least 6 historical rows to cross-validate model quality.")


class TestFitDiagnostics(unittest.TestCase):
    def test_counts_one_fit_per_fold_with_real_gp(self):
        X, y, g = _replicated(n_groups=15, reps=3)
        m = compute_model_metrics(X, y, groups=list(g))
        d = m["fit_diagnostics"]
        self.assertEqual(d["n_fits"], m["n_folds_used"])
        for key in ("n_convergence_warnings", "n_fits_with_hyperparameter_at_bound", "n_fits_noise_at_lower_bound"):
            self.assertTrue(0 <= d[key] <= d["n_fits"])

    def test_convergence_warning_is_counted_and_still_emitted(self):
        class Noisy:
            def fit(self, X, y):
                warnings.warn("lbfgs did not converge", ConvergenceWarning)
                self.m = float(np.mean(y))
                return self

            def predict(self, X, return_std=False):
                mu = np.full(len(X), self.m)
                return (mu, np.ones(len(X))) if return_std else mu

        X, y, g = _replicated(n_groups=10, reps=2)
        with mock.patch.object(mm, "build_surrogate", lambda d: Noisy()):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                cal = compute_uncertainty_calibration(X, y, groups=list(g))
        d = cal["fit_diagnostics"]
        self.assertEqual(d["n_convergence_warnings"], d["n_fits"])
        self.assertGreaterEqual(sum(issubclass(w.category, ConvergenceWarning) for w in caught), d["n_fits"])

    def test_noise_at_lower_bound_is_detected_on_noise_free_data(self):
        rng = np.random.default_rng(0)
        X = rng.uniform(0, 1, (30, 2))
        y = 10 + 5 * X[:, 0] + 3 * X[:, 1]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = compute_model_metrics(X, y)
        self.assertGreaterEqual(m["fit_diagnostics"]["n_fits_noise_at_lower_bound"], 1)


if __name__ == "__main__":
    unittest.main()
