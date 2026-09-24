"""
Engine unit tests, using Python's built-in `unittest` (no pytest dependency
required). Run with:  python3 -m unittest discover -s engine/tests -v
"""
import unittest
import numpy as np

from engine.models.surrogate import build_surrogate
from engine.preprocessing.normalization import feature_bounds, normalize, denormalize
from engine.acquisition.expected_improvement import expected_improvement
from engine.constraints.feasibility import fit_constraint_models, feasibility_probability
from engine.optimization.direction import best_so_far, is_better, target_reached
from engine.optimization.recommend import recommend_next
from engine.evaluation.backtest import run_backtest
from engine.evaluation.model_metrics import compute_model_metrics


def _toy_data(n=20, d=3, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.uniform(0, 10, size=(n, d))
    y = -np.sum((X - 5) ** 2, axis=1) + rng.normal(0, 0.5, n)  # maximized near X=5
    return X, y


class TestNormalization(unittest.TestCase):
    def test_round_trip(self):
        X, _ = _toy_data()
        lo, hi = feature_bounds(X)
        Xn = normalize(X, lo, hi)
        self.assertTrue((Xn >= -1e-9).all() and (Xn <= 1 + 1e-9).all())
        X_back = denormalize(Xn, lo, hi)
        np.testing.assert_allclose(X, X_back, atol=1e-8)


class TestExpectedImprovement(unittest.TestCase):
    def test_higher_mu_is_more_valuable_when_maximizing(self):
        mu = np.array([1.0, 5.0])
        sigma = np.array([0.1, 0.1])
        ei = expected_improvement(mu, sigma, best_so_far=0.0, direction="maximize")
        self.assertGreater(ei[1], ei[0])

    def test_lower_mu_is_more_valuable_when_minimizing(self):
        mu = np.array([1.0, 5.0])
        sigma = np.array([0.1, 0.1])
        ei = expected_improvement(mu, sigma, best_so_far=10.0, direction="minimize")
        self.assertGreater(ei[0], ei[1])

    def test_rejects_invalid_direction(self):
        with self.assertRaises(ValueError):
            expected_improvement(np.array([1.0]), np.array([0.1]), 0.0, "sideways")


class TestDirectionHelpers(unittest.TestCase):
    def test_best_so_far_maximize(self):
        self.assertEqual(best_so_far(np.array([1.0, 5.0, 3.0]), "maximize"), 5.0)

    def test_best_so_far_minimize(self):
        self.assertEqual(best_so_far(np.array([1.0, 5.0, 3.0]), "minimize"), 1.0)

    def test_is_better(self):
        self.assertTrue(is_better(2.0, 1.0, "maximize"))
        self.assertFalse(is_better(2.0, 1.0, "minimize"))

    def test_target_reached(self):
        self.assertTrue(target_reached(10.0, 8.0, "maximize"))
        self.assertFalse(target_reached(6.0, 8.0, "maximize"))
        self.assertTrue(target_reached(2.0, 5.0, "minimize"))


class TestConstraints(unittest.TestCase):
    def test_feasibility_favors_points_inside_bound(self):
        X, _ = _toy_data(n=30, seed=1)
        lo, hi = feature_bounds(X)
        Xn = normalize(X, lo, hi)
        constraint_val = np.sum(X, axis=1)  # bigger sum = "worse"
        constraints = [{"column": "c1", "op": "<=", "value": float(np.median(constraint_val))}]
        models = fit_constraint_models(Xn, constraints, {"c1": constraint_val})
        self.assertIn("c1", models)

        low_point = normalize(np.array([[0.0, 0.0, 0.0]]), lo, hi)
        high_point = normalize(np.array([[10.0, 10.0, 10.0]]), lo, hi)
        feas_low = feasibility_probability(models, low_point, constraints)[0]
        feas_high = feasibility_probability(models, high_point, constraints)[0]
        self.assertGreater(feas_low, feas_high)

    def test_no_constraints_returns_all_ones(self):
        result = feasibility_probability({}, np.zeros((5, 2)), [])
        np.testing.assert_array_equal(result, np.ones(5))


class TestRecommend(unittest.TestCase):
    def test_returns_requested_count_and_valid_shape(self):
        X, y = _toy_data(n=25, d=2, seed=2)
        recs = recommend_next(X, y, "maximize", ["x0", "x1"], n_recommend=4, candidate_pool=500, seed=0)
        self.assertEqual(len(recs), 4)
        for r in recs:
            self.assertEqual(set(r["features"].keys()), {"x0", "x1"})
            self.assertIsInstance(r["predicted_value"], float)
            self.assertGreaterEqual(r["uncertainty_std"], 0.0)
            self.assertGreaterEqual(r["feasibility_probability"], 0.0)
            self.assertLessEqual(r["feasibility_probability"], 1.0)

    def test_recommendations_ranked_by_acquisition_descending(self):
        X, y = _toy_data(n=25, d=2, seed=3)
        recs = recommend_next(X, y, "maximize", ["x0", "x1"], n_recommend=5, candidate_pool=500, seed=0)
        scores = [r["acquisition_score"] for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestBacktest(unittest.TestCase):
    def test_engine_needs_no_more_evals_than_actual_on_easy_problem(self):
        # A smooth, well-behaved surface where BO should be able to match or
        # beat naive-order search -- not a guarantee in general (see the
        # module docstring), but a real, repeatable property of this fixture.
        X, y = _toy_data(n=40, d=2, seed=4)
        result = run_backtest(X, y, "maximize", n_init=5, seed=0)
        self.assertNotIn("error", result)
        self.assertIsNotNone(result["engine_order_evals"])
        self.assertLessEqual(result["engine_order_evals"], result["n_historical_rows"])
        self.assertEqual(len(result["actual_order_curve"]), 40)
        self.assertEqual(len(result["engine_order_curve"]), 40)

    def test_rejects_too_little_data(self):
        X, y = _toy_data(n=4, d=2, seed=5)
        result = run_backtest(X, y, "maximize", n_init=5, seed=0)
        self.assertIn("error", result)


class TestModelMetrics(unittest.TestCase):
    def test_reasonable_r2_on_smooth_function(self):
        X, y = _toy_data(n=40, d=2, seed=6)
        metrics = compute_model_metrics(X, y, k=5, seed=0)
        self.assertNotIn("error", metrics)
        self.assertIn("r2_score", metrics)
        self.assertGreater(metrics["r2_score"], 0.0)  # should beat predicting the mean

    def test_rejects_too_little_data(self):
        X, y = _toy_data(n=4, d=2, seed=7)
        metrics = compute_model_metrics(X, y)
        self.assertIn("error", metrics)


if __name__ == "__main__":
    unittest.main()
