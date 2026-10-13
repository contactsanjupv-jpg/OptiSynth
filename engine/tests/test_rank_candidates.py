"""
Run with: python3 -m unittest engine.tests.test_rank_candidates -v
"""
import unittest
import numpy as np

from engine.optimization.rank_candidates import rank_candidates, ranking_sort_key


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


class TestCandidateIdentityAndDeterministicOrder(unittest.TestCase):
    """Level 1: candidates are identified by id, never by name, and ranking order is fully
    deterministic: probability, then predicted value in the favorable direction, then id."""

    def _rank(self, candidates, direction="maximize", target=3.0):
        X, y = _linear_data()
        return rank_candidates(X, y, direction, ["x0", "x1"], target_value=target, candidates=candidates, seed=0)

    def test_ids_are_echoed_on_every_result(self):
        ranked = self._rank([
            {"id": 11, "name": "a", "features": {"x0": 1.0, "x1": 0.5}},
            {"id": 12, "name": "b", "features": {"x0": 9.0, "x1": 0.5}},
        ])
        self.assertEqual({r["id"] for r in ranked}, {11, 12})
        self.assertEqual(ranked[0]["id"], 12)  # higher x0 -> higher y -> ranks first for maximize

    def test_same_name_different_inputs_stay_separate_by_id(self):
        ranked = self._rank([
            {"id": 1, "name": "Twin", "features": {"x0": 1.0, "x1": 0.5}},
            {"id": 2, "name": "Twin", "features": {"x0": 9.0, "x1": 0.5}},
        ])
        by_id = {r["id"]: r for r in ranked}
        self.assertEqual(by_id[1]["features"]["x0"], 1.0)
        self.assertEqual(by_id[2]["features"]["x0"], 9.0)
        self.assertGreater(by_id[2]["predicted_value"], by_id[1]["predicted_value"])
        self.assertEqual([r["id"] for r in ranked], [2, 1])

    def test_identical_inputs_tie_and_are_ordered_by_id_not_name_or_position(self):
        feats = {"x0": 5.0, "x1": 0.5}
        ranked = self._rank([
            {"id": 30, "name": "Alpha", "features": dict(feats)},
            {"id": 10, "name": "Zulu", "features": dict(feats)},
            {"id": 20, "name": "Mike", "features": dict(feats)},
        ])
        self.assertEqual([r["id"] for r in ranked], [10, 20, 30])
        self.assertEqual(len({r["predicted_probability"] for r in ranked}), 1)

    def test_order_does_not_depend_on_input_order(self):
        cands = [
            {"id": i, "name": f"c{i}", "features": {"x0": float(i % 5) * 2, "x1": 0.5}} for i in range(1, 13)
        ]
        forward = [r["id"] for r in self._rank(cands)]
        backward = [r["id"] for r in self._rank(list(reversed(cands)))]
        self.assertEqual(forward, backward)

    def test_without_ids_input_position_is_the_last_tie_break(self):
        feats = {"x0": 5.0, "x1": 0.5}
        ranked = self._rank([
            {"name": "Zulu", "features": dict(feats)},
            {"name": "Alpha", "features": dict(feats)},
        ])
        self.assertEqual([r["name"] for r in ranked], ["Zulu", "Alpha"])
        self.assertTrue(all(r["id"] is None for r in ranked))
        self.assertTrue(all("_input_position" not in r for r in ranked))


class TestRankingSortKey(unittest.TestCase):
    def test_probability_dominates(self):
        self.assertLess(ranking_sort_key(0.9, 1, 99, "maximize"), ranking_sort_key(0.8, 1000, 1, "maximize"))

    def test_tie_on_probability_prefers_higher_value_for_maximize(self):
        self.assertLess(ranking_sort_key(1.0, 10.0, 2, "maximize"), ranking_sort_key(1.0, 5.0, 1, "maximize"))

    def test_tie_on_probability_prefers_lower_value_for_minimize(self):
        self.assertLess(ranking_sort_key(1.0, 5.0, 2, "minimize"), ranking_sort_key(1.0, 10.0, 1, "minimize"))

    def test_tie_on_probability_and_value_prefers_lower_id(self):
        self.assertLess(ranking_sort_key(0.5, 7.0, 1, "maximize"), ranking_sort_key(0.5, 7.0, 2, "maximize"))

    def test_float_noise_is_not_a_difference(self):
        a = ranking_sort_key(0.7, 12.0, 2, "maximize")
        b = ranking_sort_key(0.7 + 1e-15, 12.0 + 1e-13, 1, "maximize")
        self.assertLess(b, a)  # treated as a tie, so the lower id (1) wins

    def test_missing_value_sorts_after_a_recorded_value_on_a_probability_tie(self):
        self.assertLess(ranking_sort_key(0.5, -1e9, 9, "maximize"), ranking_sort_key(0.5, None, 1, "maximize"))
        self.assertLess(ranking_sort_key(0.5, 1e9, 9, "minimize"), ranking_sort_key(0.5, None, 1, "minimize"))

    def test_name_is_not_an_input(self):
        import inspect
        self.assertEqual(list(inspect.signature(ranking_sort_key).parameters),
                         ["predicted_probability", "predicted_value", "tiebreak", "direction"])
