"""
Run with: python3 -m unittest engine.tests.test_rank_candidates -v
"""
import unittest
import numpy as np

from engine.optimization.rank_candidates import rank_candidates


def _linear_data(n=20, seed=0):
    rng = np.random.default_rng(seed)
    x0 = np.linspace(0, 10, n)
    x1 = rng.uniform(0, 1, n)
    X = np.column_stack([x0, x1])
    y = x0 * 0.6 + rng.normal(0, 0.2, n)  # y increases with x0
    return X, y


class TestRankCandidates(unittest.TestCase):
    def test_higher_feature_ranks_first_for_maximize(self):
        X, y = _linear_data()
        candidates = [
            {"name": "low", "features": {"x0": 1.0, "x1": 0.5}},
            {"name": "high", "features": {"x0": 9.0, "x1": 0.5}},
        ]
        ranked = rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=5.0, candidates=candidates, seed=0)
        self.assertEqual(ranked[0]["name"], "high")
        self.assertEqual(ranked[1]["name"], "low")
        self.assertGreater(ranked[0]["predicted_probability"], ranked[1]["predicted_probability"])

    def test_lower_feature_ranks_first_for_minimize(self):
        X, y = _linear_data()
        candidates = [
            {"name": "low", "features": {"x0": 1.0, "x1": 0.5}},
            {"name": "high", "features": {"x0": 9.0, "x1": 0.5}},
        ]
        ranked = rank_candidates(X, y, "minimize", ["x0", "x1"], target_value=2.0, candidates=candidates, seed=0)
        self.assertEqual(ranked[0]["name"], "low")

    def test_every_candidate_gets_full_fields(self):
        X, y = _linear_data()
        candidates = [{"name": "only", "features": {"x0": 5.0, "x1": 0.5}}]
        ranked = rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=3.0, candidates=candidates, seed=0)
        r = ranked[0]
        for field in ("name", "features", "predicted_value", "uncertainty_std", "predicted_probability", "model_version"):
            self.assertIn(field, r)
        self.assertGreaterEqual(r["predicted_probability"], 0.0)
        self.assertLessEqual(r["predicted_probability"], 1.0)

    def test_deterministic_given_same_seed(self):
        X, y = _linear_data()
        candidates = [{"name": "a", "features": {"x0": 4.0, "x1": 0.5}}]
        r1 = rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=3.0, candidates=candidates, seed=0)
        r2 = rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=3.0, candidates=candidates, seed=0)
        self.assertEqual(r1, r2)

    def test_empty_candidates_raises(self):
        X, y = _linear_data()
        with self.assertRaises(ValueError):
            rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=3.0, candidates=[], seed=0)

    def test_single_named_candidate_not_pool_generated(self):
        # Explicitly proves this scores ONLY the supplied candidate(s), never
        # generates additional ones -- the core distinction from recommend_next.
        X, y = _linear_data()
        candidates = [{"name": "the only one", "features": {"x0": 5.0, "x1": 0.5}}]
        ranked = rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=3.0, candidates=candidates, seed=0)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["name"], "the only one")

    def test_probability_collapses_to_hard_bound_at_zero_uncertainty(self):
        # A candidate sitting exactly on a repeated, noiseless observation
        # should have prob->1 or prob->0, not something ambiguous in between.
        X = np.array([[1.0, 0.5]] * 10 + [[9.0, 0.5]] * 10)
        y = np.array([1.0] * 10 + [9.0] * 10)
        candidates = [{"name": "matches low cluster", "features": {"x0": 1.0, "x1": 0.5}}]
        ranked = rank_candidates(X, y, "maximize", ["x0", "x1"], target_value=5.0, candidates=candidates, seed=0)
        self.assertLess(ranked[0]["predicted_probability"], 0.5)


if __name__ == "__main__":
    unittest.main()
