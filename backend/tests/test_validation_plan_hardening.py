"""Hardening of validation_plan.closest_records: non-finite numbers, empty feature_columns, and proof that the
results for valid finite input are exactly what they were before (golden values captured from the unmodified
function). Run:  python3 -m unittest backend.tests.test_validation_plan_hardening -v"""
import math
import random
import unittest

import numpy as np

from backend.app.services import validation_plan as vp

COLS = ["a", "b", "c"]
INF, NAN = float("inf"), float("nan")


def _rec(i, a, b, c, target, prov=None):
    return {"id": i, "features": {"a": a, "b": b, "c": c}, "target_value": target, "provenance": prov}


# 21 records, 3 modelled columns (record 21 repeats record 4's inputs with a different target: a replicate).
GOLDEN_RECORDS = [
    _rec(1, 7.74, 275.55, 4, 47.89),
    _rec(2, 0.942, 490.25, 7, 50.45),
    _rec(3, 7.861, 151.25, 7, 34.83),
    _rec(4, 9.268, 357.55, 4, 52.91),
    _rec(5, 4.434, 190.9, 1, 22.55),
    _rec(6, 8.276, 352.67, 5, 50.32),
    _rec(7, 3.545, 488.28, 4, 51.14),
    _rec(8, 1.946, 286.69, 8, 21.75),
    _rec(9, 1.543, 373.22, 8, 58.7),
    _rec(10, 3.258, 248.18, 6, 38.78),
    _rec(11, 1.895, 151.97, 6, 29.08),
    _rec(12, 6.698, 274.86, 4, 53.31),
    _rec(13, 7.003, 224.95, 7, 52.19),
    _rec(14, 3.875, 215.33, 7, 47.3),
    _rec(15, 1.398, 179.96, 7, 51.48),
    _rec(16, 6.649, 382.07, 1, 51.23),
    _rec(17, 4.589, 327.5, 1, 24.58),
    _rec(18, 6.684, 288.44, 2, 42.61),
    _rec(19, 7.65, 353.89, 5, 42.37),
    _rec(20, 3.04, 112.33, 5, 37.47),
    _rec(21, 9.268, 357.55, 4, 99.0),
]
GOLDEN_CANDIDATES = [{'a': 5.0, 'b': 300.0, 'c': 4},
 {'a': 0.0, 'b': 100.0, 'c': 1},
 {'a': 12.0, 'b': 600.0, 'c': 20},
 {'a': 9.268, 'b': 357.55, 'c': 4}]
# (record id, target, distance) for k=4, captured from the function BEFORE the hardening change.
GOLDEN_RESULTS = [
    [(12, 53.31, 0.215), (1, 47.89, 0.335), (18, 42.61, 0.351), (19, 42.37, 0.377)],
    [(5, 22.55, 0.584), (20, 37.47, 0.679), (11, 29.08, 0.762), (17, 24.58, 0.816)],
    [(13, 52.19, 2.19), (9, 58.7, 2.208), (8, 21.75, 2.255), (3, 34.83, 2.26)],
    [(4, 52.91, 0.0), (21, 99.0, 0.0), (6, 50.32, 0.186), (19, 42.37, 0.241)],
]

SMALL = [_rec(1, 0.0, 0.0, 0.0, 10.0), _rec(2, 1.0, 1.0, 1.0, 20.0), _rec(3, 0.5, 0.5, 0.5, 15.0),
         _rec(4, 0.2, 0.9, 0.1, 11.0)]
CAND = {"a": 0.4, "b": 0.4, "c": 0.4}


def _ids(out):
    return [int(o["reference"].split()[2]) for o in out]


class TestIsFiniteNumber(unittest.TestCase):
    def test_accepts_finite_ints_and_floats(self):
        for v in (0, 1, -3, 0.0, -0.0, 2.5, 1e308, -1e308, 5e-324, 10 ** 15, np.float64(1.5)):
            self.assertTrue(vp._is_finite_number(v), v)
        self.assertFalse(vp._is_finite_number(np.float32(2.0)))  # not a Python float: rejected before the change too

    def test_rejects_nan_and_both_infinities(self):
        for v in (NAN, INF, -INF, np.float64("nan"), np.float64("inf"), np.float64("-inf")):
            self.assertFalse(vp._is_finite_number(v), v)

    def test_rejects_non_numbers_bool_and_unrepresentable_ints(self):
        for v in (None, True, False, "1.5", "nan", b"1", [1], {"a": 1}, 10 ** 400, -(10 ** 400)):
            self.assertFalse(vp._is_finite_number(v), repr(v)[:20])


class TestNonFiniteValuesAreRejected(unittest.TestCase):
    def test_a_record_with_a_non_finite_feature_is_skipped_and_cannot_distort_the_rest(self):
        expected = vp.closest_records(CAND, SMALL, COLS, k=10)
        for bad in (NAN, INF, -INF):
            for col in COLS:
                poisoned = _rec(99, 0.3, 0.3, 0.3, 1.0)
                poisoned["features"][col] = bad
                out = vp.closest_records(CAND, SMALL + [poisoned], COLS, k=10)
                self.assertEqual(out, expected, (bad, col))
                self.assertNotIn(99, _ids(out))

    def test_an_infinite_record_no_longer_changes_the_scaling_of_the_others(self):
        # Before the fix an infinite value made that column's span infinite, which silently dropped the column.
        with_inf = vp.closest_records({"a": 0.4, "b": 0.4}, SMALL + [_rec(5, INF, 0.1, 0.1, 1.0)], ["a", "b"], k=4)
        without = vp.closest_records({"a": 0.4, "b": 0.4}, SMALL, ["a", "b"], k=4)
        self.assertEqual(with_inf, without)

    def test_a_candidate_with_a_non_finite_feature_gets_nothing(self):
        for bad in (NAN, INF, -INF):
            for col in COLS:
                cand = dict(CAND)
                cand[col] = bad
                self.assertEqual(vp.closest_records(cand, SMALL, COLS), [], (bad, col))

    def test_all_records_non_finite_gives_nothing(self):
        recs = [_rec(i, NAN, 0.1, 0.1, 1.0) for i in range(1, 4)] + [_rec(9, 0.1, INF, 0.1, 1.0)]
        self.assertEqual(vp.closest_records(CAND, recs, COLS), [])

    def test_non_finite_values_outside_the_modelled_columns_are_irrelevant(self):
        recs = [dict(r, features={**r["features"], "condition": NAN, "note": INF}) for r in SMALL]
        self.assertEqual(vp.closest_records(CAND, recs, COLS), vp.closest_records(CAND, SMALL, COLS))
        cand = {**CAND, "condition": NAN}
        self.assertEqual(vp.closest_records(cand, SMALL, COLS), vp.closest_records(CAND, SMALL, COLS))

    def test_integers_beyond_float_range_are_skipped_without_raising(self):
        huge = _rec(7, 10 ** 400, 0.1, 0.1, 1.0)
        self.assertEqual(vp.closest_records(CAND, SMALL + [huge], COLS), vp.closest_records(CAND, SMALL, COLS))
        self.assertEqual(vp.closest_records({**CAND, "a": 10 ** 400}, SMALL, COLS), [])

    def test_numpy_floats_that_are_finite_still_work_and_non_finite_ones_do_not(self):
        recs = [_rec(i, np.float64(a), np.float64(b), np.float64(c), t) for i, (a, b, c, t) in
                enumerate([(0.0, 0.0, 0.0, 10.0), (1.0, 1.0, 1.0, 20.0), (0.5, 0.5, 0.5, 15.0)], start=1)]
        self.assertEqual(_ids(vp.closest_records(CAND, recs, COLS)), [3, 1, 2])
        recs.append(_rec(4, np.float64("nan"), 0.1, 0.1, 1.0))
        self.assertNotIn(4, _ids(vp.closest_records(CAND, recs, COLS)))


class TestEmptyFeatureColumns(unittest.TestCase):
    def test_no_modelled_columns_means_no_comparison(self):
        for cols in ([], (), None):
            self.assertEqual(vp.closest_records(CAND, SMALL, cols), [], repr(cols))
            self.assertEqual(vp.closest_records({}, SMALL, cols, k=10), [], repr(cols))
            self.assertEqual(vp.closest_records(CAND, [], cols), [], repr(cols))

    def test_it_no_longer_returns_arbitrary_records_at_distance_zero(self):
        out = vp.closest_records(CAND, SMALL, [])
        self.assertEqual(out, [])  # before the guard: the first k records by id, each at distance 0.0

    def test_other_empty_inputs_still_give_nothing(self):
        self.assertEqual(vp.closest_records(CAND, [], COLS), [])
        self.assertEqual(vp.closest_records(CAND, SMALL, COLS, k=0), [])
        self.assertEqual(vp.closest_records(CAND, SMALL, COLS, k=-3), [])
        self.assertEqual(vp.closest_records({}, SMALL, COLS), [])


class TestValidBehaviourIsUnchanged(unittest.TestCase):
    def test_golden_results_from_the_unmodified_function(self):
        for cand, expected in zip(GOLDEN_CANDIDATES, GOLDEN_RESULTS):
            out = vp.closest_records(cand, GOLDEN_RECORDS, COLS, k=4)
            self.assertEqual([(int(o["reference"].split()[2]), o["target"], o["distance"]) for o in out], expected)
            self.assertTrue(all(o["reference"].startswith("evidence record ") for o in out))
            self.assertEqual(set(out[0]), {"reference", "target", "distance"})

    def test_matches_an_independent_implementation_of_the_documented_formula(self):
        rng = random.Random(7)
        for _ in range(25):
            n, d = rng.randint(4, 15), rng.randint(1, 4)
            cols = [f"x{j}" for j in range(d)]
            recs = [{"id": i + 1, "features": {c: round(rng.uniform(-50, 50), 4) for c in cols},
                     "target_value": rng.uniform(0, 100), "provenance": None} for i in range(n)]
            cand = {c: rng.uniform(-60, 60) for c in cols}
            spans = {c: max(r["features"][c] for r in recs) - min(r["features"][c] for r in recs) for c in cols}
            ref = sorted(((math.sqrt(sum(((cand[c] - r["features"][c]) / spans[c]) ** 2 for c in cols if spans[c] > 0)), r["id"])
                          for r in recs))[:3]
            got = vp.closest_records(cand, recs, cols)
            self.assertEqual(_ids(got), [i for _, i in ref])
            self.assertEqual([o["distance"] for o in got], [round(dist, 3) for dist, _ in ref])

    def test_distance_ordering_and_k_limit(self):
        # by hand (every column spans 0..1): record 1 -> 0.05, record 3 -> sqrt(.45^2+.5^2+.5^2)=0.838, record 4 -> 0.918
        out = vp.closest_records({"a": 0.05, "b": 0.0, "c": 0.0}, SMALL, COLS, k=2)
        self.assertEqual(_ids(out), [1, 3])
        self.assertEqual([o["distance"] for o in out], [0.05, 0.838])
        self.assertLessEqual(out[0]["distance"], out[1]["distance"])
        self.assertEqual(len(vp.closest_records(CAND, SMALL, COLS)), 3)          # default k
        self.assertEqual(len(vp.closest_records(CAND, SMALL, COLS, k=50)), 4)    # never more than exist

    def test_exact_ties_break_on_the_lower_record_id(self):
        recs = [_rec(9, 0.0, 1.0, 0.0, 1.0), _rec(7, 1.0, 0.0, 0.0, 2.0), _rec(8, 0.0, 0.0, 0.0, 3.0), _rec(2, 1.0, 1.0, 1.0, 4.0)]
        out = vp.closest_records({"a": 0.5, "b": 0.5, "c": 0.0}, recs, COLS, k=4)
        self.assertEqual(_ids(out)[:3], [7, 8, 9])
        self.assertEqual(len({o["distance"] for o in out[:3]}), 1)

    def test_result_does_not_depend_on_the_order_the_records_are_given(self):
        reference = vp.closest_records(CAND, GOLDEN_RECORDS, COLS, k=5)
        shuffled = list(GOLDEN_RECORDS)
        random.Random(3).shuffle(shuffled)
        self.assertEqual(vp.closest_records(CAND, shuffled, COLS, k=5), reference)

    def test_features_are_scaled_by_range_and_constant_columns_contribute_nothing(self):
        recs = [_rec(1, 0.0, 0.0, 5.0, 1.0), _rec(2, 0.001, 1000.0, 5.0, 2.0)]
        self.assertEqual(_ids(vp.closest_records({"a": 0.0005, "b": 10.0, "c": 99.0}, recs, COLS, k=1)), [1])

    def test_missing_and_non_numeric_values_are_still_skipped(self):
        bad = [{"id": 5, "features": {"a": 0.4, "b": 0.4}, "target_value": 1.0, "provenance": None},     # no c
               _rec(6, "0.4", 0.4, 0.4, 1.0), _rec(7, True, 0.4, 0.4, 1.0), _rec(8, None, 0.4, 0.4, 1.0),
               {"id": 9, "features": None, "target_value": 1.0, "provenance": None}]
        self.assertEqual(vp.closest_records(CAND, SMALL + bad, COLS, k=10), vp.closest_records(CAND, SMALL, COLS, k=10))
        self.assertEqual(vp.closest_records({"a": 0.4, "b": 0.4}, SMALL, COLS), [])
        self.assertEqual(vp.closest_records({**CAND, "a": True}, SMALL, COLS), [])

    def test_ints_and_provenance_formatting_are_unchanged(self):
        recs = [_rec(1, 0, 0, 0, 10, {"location": "history.csv", "row": 7}), _rec(2, 4, 4, 4, 20, None)]
        out = vp.closest_records({"a": 1, "b": 1, "c": 1}, recs, COLS, k=2)
        self.assertEqual(out[0]["reference"], "evidence record 1 (history.csv row 7)")
        self.assertEqual(out[1]["reference"], "evidence record 2")
        self.assertEqual((out[0]["target"], out[0]["distance"]), (10, 0.433))


if __name__ == "__main__":
    unittest.main()
