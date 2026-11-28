"""Level 6 decision engine: constraints, unknown handling, Pareto analysis, constraint impact and explanations.
Run:  python3 -m unittest engine.tests.test_decision_engine -v"""
import itertools
import random
import re
import unittest

from engine.decision import analysis as A, constraints as C, pareto as P


def con(i, op, value, ref="attr:x", kind="hard", name=None, unit=None):
    return {"id": i, "name": name or f"c{i}", "kind": kind, "category": "customer_rule", "ref": ref, "operator": op, "value": value, "unit": unit, "rationale": "r"}


def fact(v, sd=None):
    return {"value": v, "sd": sd}


class TestEvaluate(unittest.TestCase):
    def state(self, op, value, observed, sd=None):
        return C.evaluate(con(1, op, value), fact(observed, sd))["state"]

    def test_exact_numeric_operators_on_both_sides_of_the_limit(self):
        cases = [(">=", 5, 5, "satisfied"), (">=", 5, 4.99, "violated"), (">", 5, 5, "violated"), (">", 5, 5.01, "satisfied"),
                 ("<=", 5, 5, "satisfied"), ("<=", 5, 5.01, "violated"), ("<", 5, 5, "violated"), ("<", 5, 4.99, "satisfied"),
                 ("between", [2, 4], 2, "satisfied"), ("between", [2, 4], 4, "satisfied"), ("between", [2, 4], 4.1, "violated"),
                 ("between", [2, 4], 1.9, "violated"), ("==", 3, 3, "satisfied"), ("==", 3, 3.1, "violated")]
        for op, limit, observed, expected in cases:
            self.assertEqual(self.state(op, limit, observed), expected, (op, limit, observed))

    def test_a_predicted_value_whose_interval_crosses_the_limit_is_unknown_not_passing(self):
        r = C.evaluate(con(1, ">=", 30), fact(31, 2.0))  # interval about 27.1 to 34.9
        self.assertEqual(r["state"], "unknown")
        self.assertTrue(r["point_estimate_met"])
        self.assertIn("neither shown to be met nor shown to be missed", r["detail"])
        self.assertEqual(C.evaluate(con(1, ">=", 30), fact(40, 2.0))["state"], "satisfied")
        self.assertEqual(C.evaluate(con(1, ">=", 30), fact(20, 2.0))["state"], "violated")
        self.assertEqual(C.evaluate(con(1, "<=", 30), fact(29, 2.0))["state"], "unknown")
        self.assertEqual(C.evaluate(con(1, "between", [10, 20]), fact(15, 5.0))["state"], "unknown")
        self.assertEqual(C.evaluate(con(1, "between", [10, 20]), fact(15, 1.0))["state"], "satisfied")

    def test_exact_match_cannot_be_shown_for_a_prediction(self):
        self.assertEqual(C.evaluate(con(1, "==", 3), fact(3, 0.5))["state"], "unknown")

    def test_a_missing_value_is_unknown_with_a_reason(self):
        r = C.evaluate(con(1, "<=", 5), None)
        self.assertEqual(r["state"], "unknown")
        self.assertEqual(r["detail"], "No value is recorded.")
        self.assertEqual(C.evaluate(con(1, "<=", 5), None, "The prediction is out of date.")["detail"], "The prediction is out of date.")

    def test_text_boolean_and_list_operators(self):
        e = lambda op, limit, v: C.evaluate(con(1, op, limit), fact(v))["state"]  # noqa: E731
        self.assertEqual(e("equals", "Acme", "acme"), "satisfied")
        self.assertEqual(e("not_equals", "Acme", "Acme"), "violated")
        self.assertEqual(e("in", ["Acme", "Beta"], "beta"), "satisfied")
        self.assertEqual(e("not_in", ["Acme"], "Gamma"), "satisfied")
        self.assertEqual(e("equals", True, True), "satisfied")
        self.assertEqual(e("equals", True, False), "violated")
        self.assertEqual(e("excludes_any", ["PFAS", "Cr6"], ["resin", "pfas"]), "violated")
        self.assertEqual(e("excludes_any", ["PFAS"], ["resin"]), "satisfied")
        self.assertEqual(e("includes_any", ["resin"], ["resin", "talc"]), "satisfied")

    def test_a_value_of_the_wrong_type_is_unknown(self):
        self.assertEqual(C.evaluate(con(1, ">=", 5), fact("abc"))["state"], "unknown")
        self.assertEqual(C.evaluate(con(1, ">=", 5), fact(float("nan")))["state"], "unknown")

    def test_margin_is_headroom_positive_and_shortfall_negative(self):
        self.assertAlmostEqual(C.evaluate(con(1, ">=", 5), fact(7))["margin"], 2)
        self.assertAlmostEqual(C.evaluate(con(1, "<=", 5), fact(7))["margin"], -2)
        self.assertAlmostEqual(C.evaluate(con(1, "between", [2, 4]), fact(3.5))["margin"], 0.5)


def cand(i, name, **facts):
    return {"id": i, "name": name, "facts": {k: (v if isinstance(v, dict) else fact(v)) for k, v in facts.items()}, "context": {}}


OBJ = [{"id": 1, "name": "Strength", "ref": "predicted:t", "direction": "maximize"}, {"id": 2, "name": "Cost", "ref": "attr:cost", "direction": "minimize"}]


class TestPareto(unittest.TestCase):
    def members(self, rows):
        return {i: {"predicted:t": {"value": s, "interval": None}, "attr:cost": {"value": c, "interval": None}} for i, (s, c) in rows.items()}

    def test_hand_checked_front_and_layers(self):
        # id: (strength, cost). 1 and 2 trade off. 3 is dominated by 1. 4 is dominated by 3 and 1.
        out = P.analyse(self.members({1: (40, 4), 2: (35, 3), 3: (36, 4.5), 4: (30, 5)}), OBJ)
        self.assertEqual([out[i]["layer"] for i in (1, 2, 3, 4)], [1, 1, 2, 3])
        self.assertEqual(out[3]["dominated_by"], [1])
        self.assertEqual(sorted(out[4]["dominated_by"]), [1, 2, 3])

    def test_ties_are_both_on_the_front_and_direction_is_respected(self):
        out = P.analyse(self.members({1: (40, 4), 2: (40, 4)}), OBJ)
        self.assertEqual((out[1]["layer"], out[2]["layer"]), (1, 1))
        better = P.analyse(self.members({1: (41, 4), 2: (40, 4)}), OBJ)
        self.assertEqual(better[2]["dominated_by"], [1])  # higher strength wins, so direction maximize is honoured
        cheaper = P.analyse(self.members({1: (40, 3), 2: (40, 4)}), OBJ)
        self.assertEqual(cheaper[2]["dominated_by"], [1])  # lower cost wins

    def test_single_objective_front_is_the_best_value(self):
        m = {1: {"predicted:t": {"value": 5, "interval": None}}, 2: {"predicted:t": {"value": 9, "interval": None}}, 3: {"predicted:t": {"value": 9, "interval": None}}}
        out = P.analyse(m, [OBJ[0]])
        self.assertEqual([out[i]["layer"] for i in (1, 2, 3)], [2, 1, 1])

    def test_matches_a_brute_force_definition_on_random_data(self):
        rnd = random.Random(1)
        for _ in range(20):
            rows = {i: (rnd.randint(0, 6), rnd.randint(0, 6)) for i in range(1, 9)}
            out = P.analyse(self.members(rows), OBJ)
            for a, b in itertools.permutations(rows, 2):
                sa, ca = rows[a]
                sb, cb = rows[b]
                dom = sa >= sb and ca <= cb and (sa > sb or ca < cb)
                self.assertEqual(a in out[b]["dominated_by"], dom)
            front = {i for i in rows if out[i]["layer"] == 1}
            self.assertEqual(front, {i for i in rows if not any(i != j and rows[j][0] >= rows[i][0] and rows[j][1] <= rows[i][1] and rows[j] != rows[i] for j in rows)})
            for i in front:
                self.assertEqual(out[i]["dominated_by"], [])

    def test_dominance_is_confident_only_when_intervals_separate(self):
        m = {1: {"predicted:t": {"value": 40, "interval": [38, 42]}, "attr:cost": {"value": 4, "interval": None}},
             2: {"predicted:t": {"value": 30, "interval": [28, 32]}, "attr:cost": {"value": 5, "interval": None}},
             3: {"predicted:t": {"value": 39, "interval": [30, 48]}, "attr:cost": {"value": 6, "interval": None}}}
        out = P.analyse(m, OBJ)
        self.assertEqual(out[2]["confidently_dominated_by"], [1])  # 38 > 32 and cheaper
        self.assertEqual(out[3]["dominated_by"], [1])
        self.assertEqual(out[3]["confidently_dominated_by"], [])  # 3's interval reaches 48: not firm

    def test_result_does_not_depend_on_input_order(self):
        rows = {1: (40, 4), 2: (35, 3), 3: (36, 4.5), 4: (30, 5)}
        a = P.analyse(self.members(rows), OBJ)
        b = P.analyse(self.members(dict(reversed(list(rows.items())))), OBJ)
        self.assertEqual(a, b)


CONS = [con(1, ">=", 30, "predicted:t", name="Strength at least 30", unit="MPa"), con(2, "<=", 5, "attr:cost", name="Cost at most 5", unit="USD")]


def build(*cands, cons=CONS, objs=OBJ):
    return A.analyse(list(cands), cons, objs)


class TestAnalysis(unittest.TestCase):
    def setUp(self):
        self.out = build(
            cand(1, "A", **{"predicted:t": fact(40, 1), "attr:cost": 4}),
            cand(2, "B", **{"predicted:t": fact(35, 1), "attr:cost": 3}),
            cand(3, "C", **{"predicted:t": fact(36, 1), "attr:cost": 4.5}),
            cand(4, "D", **{"predicted:t": fact(25, 1), "attr:cost": 2}),
            cand(5, "E", **{"predicted:t": fact(33, 10)}),
            cand(6, "F", **{"predicted:t": fact(45, 1), "attr:cost": 9}),
        )

    def c(self, i):
        return self.out["candidates"][str(i)]

    def test_groups_and_unknown_is_never_feasible(self):
        self.assertEqual({i: self.c(i)["group"] for i in range(1, 7)},
                         {1: "feasible", 2: "feasible", 3: "feasible", 4: "rejected", 5: "unresolved", 6: "rejected"})
        self.assertEqual(self.c(5)["unknown_hard"], [1, 2])  # both are unknown, so neither passes
        self.assertEqual(self.c(5)["pareto"]["status"], "not_eligible")

    def test_only_feasible_candidates_reach_the_front(self):
        self.assertEqual(self.out["summary"]["front"], [1, 2])
        self.assertEqual(self.c(3)["pareto"]["status"], "dominated")
        self.assertEqual(self.c(6)["pareto"]["status"], "not_eligible")  # strongest, but too expensive: it is not on the front

    def test_a_rejected_candidate_with_the_best_value_is_still_rejected(self):
        self.assertEqual(self.c(6)["group"], "rejected")
        self.assertIn("Rejected", self.c(6)["explanation"]["headline"])

    def test_constraint_impact_finds_the_dominating_constraints(self):
        impact = {x["constraint_id"]: x for x in self.out["summary"]["constraint_impact"]}
        self.assertEqual(impact[1]["sole_blocker_for"], [4])
        self.assertEqual(impact[2]["sole_blocker_for"], [6])
        self.assertEqual(impact[2]["would_change_if_removed"], {"6": "feasible"})  # E stays unresolved: its strength is still unknown
        self.assertEqual(impact[2]["violated_by"], 1)

    def test_unresolved_candidate_has_a_provisional_position_and_asks_for_the_missing_value(self):
        e = self.c(5)["explanation"]
        self.assertTrue(any("Cost at most 5" in line for line in e["could_change"]))
        self.assertTrue(any("No value is recorded" in line for line in e["could_change"]))
        miss = {m["needed_for"] for m in self.c(5)["missing_information"]}
        self.assertEqual(miss, {"Strength at least 30", "Cost at most 5", "Cost"})

    def test_explanations_cover_why_promising_why_rejected_what_dominates_and_what_could_change(self):
        a, d = self.c(1)["explanation"], self.c(4)["explanation"]
        self.assertIn("on the Pareto front", a["headline"])
        self.assertTrue(a["promising"])
        self.assertTrue(d["rejected"])
        self.assertEqual(d["dominating_constraints"][0]["name"], "Strength at least 30")
        self.assertIn("only hard constraint it violates", d["dominating_constraints"][0]["effect"])
        self.assertIn("percent of the limit", d["rejected"][0])

    def test_nothing_ever_calls_a_candidate_qualified(self):
        for i in range(1, 7):
            e = self.c(i)["explanation"]
            text = " ".join([e["headline"], *e["promising"], *e["rejected"], *e["supported_by"], *e["could_change"]])
            self.assertNotRegex(text, r"(?i)\bqualified\b", i)
            self.assertTrue(e["qualification"].startswith("Not qualified."))
        top = self.c(1)["explanation"]["qualification"]
        self.assertIn("not qualification", top)
        self.assertIn("No physical outcome is recorded", top)

    def test_physical_outcomes_are_reported_without_qualifying(self):
        c = cand(1, "A", **{"predicted:t": fact(40, 1), "attr:cost": 4})
        c["context"] = {"physical": {"passed": 2, "failed": 0}}
        q = build(c)["candidates"]["1"]["explanation"]["qualification"]
        self.assertTrue(q.startswith("Not qualified."))
        self.assertIn("2 met the specification", q)
        self.assertIn("does not by itself qualify", q)

    def test_soft_constraints_are_tradeoffs_not_filters_and_not_weights(self):
        soft = con(3, "<=", 3.5, "attr:cost", kind="soft", name="Prefer cost at most 3.5")
        out = build(cand(1, "A", **{"predicted:t": fact(40, 1), "attr:cost": 4}), cons=CONS + [soft])
        r = out["candidates"]["1"]
        self.assertEqual(r["group"], "feasible")
        self.assertEqual(r["soft_summary"], {"met": 0, "missed": 1, "unknown": 0, "total": 1})
        self.assertTrue(any("Soft trade off" in x for x in r["explanation"]["could_change"]))
        self.assertNotIn("weight", " ".join(str(k) for k in r).lower())
        self.assertIn("No weights", out["summary"]["method"])

    def test_missing_objective_value_means_not_compared_never_zero(self):
        out = build(cand(1, "A", **{"predicted:t": fact(40, 1), "attr:cost": 4}), cand(2, "B", **{"predicted:t": fact(50, 1)}),
                    cons=[con(1, ">=", 30, "predicted:t", name="S")])
        self.assertEqual(out["candidates"]["2"]["group"], "feasible")
        self.assertEqual(out["candidates"]["2"]["pareto"]["status"], "not_comparable")
        self.assertEqual(out["candidates"]["2"]["pareto"]["missing"], ["Cost"])
        self.assertEqual(out["candidates"]["1"]["pareto"]["status"], "front")

    def test_no_objectives_means_no_trade_off_analysis(self):
        out = build(cand(1, "A", **{"predicted:t": fact(40, 1), "attr:cost": 4}), objs=[])
        self.assertEqual(out["candidates"]["1"]["pareto"]["status"], "no_objectives")
        self.assertEqual(out["summary"]["front"], [])

    def test_unavailable_reason_is_carried_to_the_explanation(self):
        c = cand(1, "A", **{"attr:cost": 4})
        c["unavailable"] = {"predicted:t": "The prediction is out of date because inputs or evidence changed."}
        out = build(c)["candidates"]["1"]
        self.assertEqual(out["group"], "unresolved")
        self.assertTrue(any("out of date" in m["reason"] for m in out["missing_information"]))

    def test_dominated_on_point_estimates_only_is_said_plainly(self):
        out = build(cand(1, "A", **{"predicted:t": fact(40, 5), "attr:cost": 4}), cand(2, "B", **{"predicted:t": fact(39, 5), "attr:cost": 5}),
                    cons=[])
        e = out["candidates"]["2"]["explanation"]["headline"]
        self.assertIn("dominated by A on point estimates only", e)
        self.assertEqual(out["summary"]["not_confidently_dominated"], [1, 2])

    def test_deterministic_and_independent_of_candidate_order(self):
        cands = [cand(i, f"N{i}", **{"predicted:t": fact(30 + i, 1), "attr:cost": 6 - i / 2}) for i in range(1, 6)]
        a = A.analyse(cands, CONS, OBJ)
        b = A.analyse(list(reversed(cands)), CONS, OBJ)
        self.assertEqual(a, b)

    def test_explanation_copy_has_no_dashes_or_decorative_separators(self):
        for i in range(1, 7):
            e = self.c(i)["explanation"]
            text = " ".join([e["headline"], *e["promising"], *e["rejected"], *e["supported_by"], *e["could_change"], e["qualification"]])
            self.assertNotIn("\u2014", text)
            self.assertNotIn(" -- ", text)


if __name__ == "__main__":
    unittest.main()
