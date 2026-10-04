"""
Candidate canonical-unit discipline and the evidence-sufficiency rules.
Pure rule tests first, then API edge cases (own isolated database).
Run:  python3 -m unittest backend.tests.test_candidate_units_and_sufficiency -v
"""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.services import change_case_rules as R  # noqa: E402
from backend.app.services import evidence_rules as ER  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import SPEC, SPEC_U, IsolatedApiTestCase  # noqa: E402

SPEC_UNITS = {**SPEC, "units": {"cure_temp_c": "degC", "salt_spray_hours": "h"}}


class TestConvertCandidateInputs(unittest.TestCase):
    def test_no_units_declared_and_none_given_is_unchanged(self):
        out, rec, problems = ER.convert_candidate_inputs(SPEC, {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, None)
        self.assertEqual((out, problems), ({"crosslinker_ratio": 0.1, "cure_temp_c": 150}, []))

    def test_unit_required_where_the_case_declares_one(self):
        _, _, problems = ER.convert_candidate_inputs(SPEC_UNITS, {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, {})
        self.assertEqual(len(problems), 1)
        self.assertIn("a unit is required", problems[0])

    def test_equivalent_unit_converts_exactly_and_keeps_the_original_entry(self):
        spec = {"feature_columns": ["t"], "target_metric": "y", "units": {"t": "h"}}
        out, rec, problems = ER.convert_candidate_inputs(spec, {"t": 1440}, {"t": "min"})
        self.assertEqual((out["t"], problems), (24.0, []))
        self.assertEqual(rec["t"], {"value": 1440, "unit": "min", "converted_value": 24.0, "converted_unit": "h"})

    def test_same_unit_different_spelling_is_accepted(self):
        out, _, problems = ER.convert_candidate_inputs(SPEC_UNITS, {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, {"cure_temp_c": "°C"})
        self.assertEqual((out["cure_temp_c"], problems), (150, []))

    def test_incompatible_and_unknown_units_are_refused(self):
        for unit in ("K", "°F", "%", "furlongs"):
            _, _, problems = ER.convert_candidate_inputs(SPEC_UNITS, {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, {"cure_temp_c": unit})
            self.assertTrue(problems, unit)

    def test_unit_for_a_field_the_case_cannot_check_is_refused_not_ignored(self):
        _, _, problems = ER.convert_candidate_inputs(SPEC_UNITS, {"crosslinker_ratio": 0.1, "cure_temp_c": 150},
                                                    {"cure_temp_c": "degC", "crosslinker_ratio": "wt%"})
        self.assertEqual(len(problems), 1)
        self.assertIn("no canonical unit", problems[0])

    def test_unit_for_a_non_feature_is_refused(self):
        _, _, problems = ER.convert_candidate_inputs(SPEC, {"crosslinker_ratio": 0.1, "cure_temp_c": 150}, {"colour": "red"})
        self.assertIn("not a feature", problems[0])


def rows(n):
    return [{"features": {"crosslinker_ratio": a, "cure_temp_c": b}, "target_value": c} for a, b, c in F.base_rows(n)]


def cand(name, ratio=0.1, temp=150):
    return {"candidate_name": name, "properties": {"crosslinker_ratio": ratio, "cure_temp_c": temp}}


class TestAssessEvidenceSufficiency(unittest.TestCase):
    def _codes(self, a, status):
        return [c["code"] for c in a["checks"] if c["status"] == status]

    def test_good_evidence_can_rank_and_every_check_is_explained(self):
        a = R.assess_evidence_sufficiency(SPEC_UNITS, rows(12), [cand("A", 0.1, 150)], True, {"flags": []})
        self.assertTrue(a["can_rank"])
        self.assertEqual(a["blocking"], [])
        self.assertTrue(all(c["detail"] and c["title"] for c in a["checks"]))
        self.assertEqual(a["summary"], {"distinct_rows": 12, "features": 2, "candidates": 1})

    def test_no_dataset_blocks_and_says_what_to_do(self):
        a = R.assess_evidence_sufficiency(SPEC, [], [cand("A")], False)
        self.assertFalse(a["can_rank"])
        self.assertEqual(self._codes(a, "fail"), ["DATASET_PRESENT"])

    def test_distinct_rows_not_raw_rows_decide_sufficiency(self):
        a = R.assess_evidence_sufficiency(SPEC, rows(6) + rows(6), [cand("A")], True, {"flags": []})
        self.assertIn("MIN_DISTINCT_ROWS", self._codes(a, "fail"))
        self.assertEqual(a["summary"]["distinct_rows"], 6)

    def test_underdetermined_fit_is_blocked(self):
        spec = {"feature_columns": [f"f{i}" for i in range(9)], "target_metric": "y", "target_value": 1, "direction": "maximize"}
        r = [{"features": {f"f{j}": (i + 1) * (j + 1) + j for j in range(9)}, "target_value": i} for i in range(9)]
        a = R.assess_evidence_sufficiency(spec, r, [{"candidate_name": "A", "properties": r[0]["features"]}], True, {"flags": []})
        self.assertEqual(self._codes(a, "fail"), ["ROWS_VS_FEATURES"])
        r10 = r + [{"features": {f"f{j}": 50 + j * 7 for j in range(9)}, "target_value": 99}]
        a10 = R.assess_evidence_sufficiency(spec, r10, [{"candidate_name": "A", "properties": r[0]["features"]}], True, {"flags": []})
        self.assertNotIn("ROWS_VS_FEATURES", self._codes(a10, "fail"))

    def test_constant_feature_blocks(self):
        r = [{"features": {"crosslinker_ratio": 0.1, "cure_temp_c": 140 + i}, "target_value": i} for i in range(10)]
        a = R.assess_evidence_sufficiency(SPEC, r, [cand("A")], True, {"flags": []})
        self.assertIn("CONSTANT_COLUMNS", self._codes(a, "fail"))

    def test_identical_targets_warn_but_do_not_block(self):
        r = [{"features": x["features"], "target_value": 100.0} for x in rows(12)]
        a = R.assess_evidence_sufficiency(SPEC, r, [cand("A")], True, {"flags": []})
        self.assertTrue(a["can_rank"])
        self.assertIn("TARGET_VARIATION", self._codes(a, "warn"))

    def test_candidate_problems_block(self):
        none = R.assess_evidence_sufficiency(SPEC, rows(12), [], True, {"flags": []})
        self.assertIn("CANDIDATES_PRESENT", self._codes(none, "fail"))
        bad = R.assess_evidence_sufficiency(SPEC, rows(12), [{"candidate_name": "A", "properties": {"crosslinker_ratio": 0.1}}], True, {"flags": []})
        self.assertIn("CANDIDATE_INPUTS_COMPLETE", self._codes(bad, "fail"))

    def test_outside_range_candidates_warn_by_name_without_blocking(self):
        a = R.assess_evidence_sufficiency(SPEC, rows(12), [cand("In", 0.1, 150), cand("Far", 0.9, 400)], True, {"flags": []})
        self.assertTrue(a["can_rank"])
        w = next(c for c in a["warnings"] if c["code"] == "CANDIDATES_OUTSIDE_RANGE")
        self.assertIn("Far", w["detail"])
        self.assertNotIn("In,", w["detail"])

    def test_legacy_dataset_and_undeclared_units_are_visible_warnings(self):
        legacy = R.assess_evidence_sufficiency(SPEC, rows(12), [cand("A")], True, None)
        self.assertIn("REVIEW_RECORD", self._codes(legacy, "warn"))
        nounits = R.assess_evidence_sufficiency(SPEC, rows(12), [cand("A")], True, {"flags": []})
        self.assertIn("UNITS_DECLARED", self._codes(nounits, "warn"))
        declared = R.assess_evidence_sufficiency(SPEC_UNITS, rows(12), [cand("A")], True, {"flags": []})
        self.assertIn("UNITS_DECLARED", self._codes(declared, "warn"))  # crosslinker_ratio still has no unit
        full = {**SPEC, "units": {"crosslinker_ratio": "wt%", "cure_temp_c": "degC", "salt_spray_hours": "h"}}
        self.assertEqual(self._codes(R.assess_evidence_sufficiency(full, rows(12), [cand("A")], True, {"flags": []}), "pass").count("UNITS_DECLARED"), 1)


class TestCandidateApi(IsolatedApiTestCase):
    dir_name = "_test_env_cand"

    def test_case_without_declared_units_behaves_exactly_as_before(self):
        org_id, client, case_id = self.setup_case("NoUnits")
        r = client.post(f"/api/change-cases/{case_id}/candidates",
                        json={"name": "A", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}})
        self.assertEqual(r.status_code, 201, r.text)
        listed = client.get(f"/api/change-cases/{case_id}/candidates").json()[0]
        self.assertEqual(listed["properties"], {"crosslinker_ratio": 0.1, "cure_temp_c": 150})
        self.assertIsNone(listed["input_record"])  # nothing to record

    def test_conversion_is_stored_canonical_with_the_original_entry_kept(self):
        _, client, case_id = self.setup_case("Convert", {**SPEC, "units": {"cure_temp_c": "degC", "salt_spray_hours": "h"}})
        r = client.post(f"/api/change-cases/{case_id}/candidates",
                        json={"name": "A", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 160}, "units": {"cure_temp_c": "°C"}})
        self.assertEqual(r.status_code, 201, r.text)
        listed = client.get(f"/api/change-cases/{case_id}/candidates").json()[0]
        self.assertEqual(listed["properties"]["cure_temp_c"], 160)
        self.assertEqual(listed["input_record"]["cure_temp_c"]["unit"], "°C")

    def test_a_refused_candidate_is_not_stored(self):
        _, client, case_id = self.setup_case("Refused", SPEC_U)
        r = client.post(f"/api/change-cases/{case_id}/candidates",
                        json={"name": "A", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 160}})
        self.assertEqual(r.status_code, 400)
        self.assertIn("a unit is required", r.json()["error"])
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/candidates").json(), [])

    def test_non_finite_and_missing_features_still_refused_with_units(self):
        _, client, case_id = self.setup_case("Finite", SPEC_U)
        missing = client.post(f"/api/change-cases/{case_id}/candidates",
                              json={"name": "A", "features": {"crosslinker_ratio": 0.1}, "units": {"cure_temp_c": "degC"}})
        self.assertEqual(missing.status_code, 400)


if __name__ == "__main__":
    unittest.main()
