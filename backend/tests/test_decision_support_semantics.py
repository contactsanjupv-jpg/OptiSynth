"""
Decision-support semantics: the status states ONLY what it checks.

`within_data_range` (formerly `evidence_supported` / "Evidence-supported") is a per-feature range check. A real
validation run showed candidates in that state with severe misses (predicted 54 MPa, measured 8 MPa), so these
tests pin what the status must and must not claim, through the pure rules, the API, the ranking, the report and
the frontend sources:

  * an in-range candidate never receives a state that implies physical evidence, compliance or qualification;
  * a high probability / tiny uncertainty cannot change the status, and a recorded "passed" outcome cannot either;
  * the status is derived at read time and never persisted, and an unrecognised / retired value fails safe;
  * the API, the ranking and the DOCX report give the same label and the same stated limits;
  * missing or customer-entered evidence is never shown as verified compliance.

Not covered because it is not implemented: a joint (nearest-neighbour) applicability check. See the handover
notes: it was tested offline and did not flag the candidates it would need to.

Run:  python3 -m unittest backend.tests.test_decision_support_semantics -v
"""
import inspect
import json
import os
import pathlib
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.services import change_case_rules as rules  # noqa: E402
from backend.app.services import ranking_evidence_rules as ranking_rules  # noqa: E402
from backend.app.services import report_builder  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import IsolatedApiTestCase  # noqa: E402

RETIRED = ("evidence_supported", "Evidence-supported", "evidence-supported")
WITHIN = {"status": rules.DOMAIN_WITHIN, "features": {}}
NEAR = {"status": rules.DOMAIN_NEAR_EDGE, "features": {}}
OUTSIDE = {"status": rules.DOMAIN_OUTSIDE, "features": {}}
HISTORY = F.base_rows(14)  # crosslinker 0.080-0.145, cure temp 140-179 C
IN_RANGE = {"crosslinker_ratio": 0.12, "cure_temp_c": 158}
IN_RANGE_2 = {"crosslinker_ratio": 0.10, "cure_temp_c": 150}
FAR = {"crosslinker_ratio": 0.40, "cure_temp_c": 300}
FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend"


class TestStatusRules(unittest.TestCase):
    def test_in_range_state_is_named_and_described_for_what_it_checks(self):
        ds = rules.derive_decision_support(WITHIN)
        self.assertEqual(ds["status"], "within_data_range")
        self.assertEqual(ds["label"], "Within data range")
        self.assertEqual(ds["scope"], "per_feature_range_check")
        for phrase in ("per-feature check only", "not physical evidence", "not a qualification",
                       "Physical validation is still required"):
            self.assertIn(phrase, ds["statement"])
        for retired in RETIRED:
            self.assertNotIn(retired, json.dumps(ds))

    def test_every_state_lists_what_it_does_not_establish(self):
        for coverage in (WITHIN, NEAR, OUTSIDE, None, {"status": "nonsense"}):
            ds = rules.derive_decision_support(coverage)
            self.assertEqual(set(ds["not_established"]), {
                "joint_input_space_coverage", "model_accuracy_for_this_candidate", "physical_performance",
                "requirement_compliance", "qualification"})
            self.assertIn("validat", ds["statement"].lower())  # "validate physically" / "validation is required"
            for retired in RETIRED:
                self.assertNotIn(retired, json.dumps(ds))

    def test_status_is_a_function_of_range_coverage_alone(self):
        # structurally: the only inputs are the coverage and whether a prediction exists, so a probability, an
        # uncertainty, a model metric or a recorded outcome has no way to reach it
        self.assertEqual(list(inspect.signature(rules.derive_decision_support).parameters),
                         ["domain_coverage", "has_prediction"])
        self.assertIsNone(rules.derive_decision_support(WITHIN, has_prediction=False))

    def test_missing_unknown_or_retired_values_fail_safe(self):
        for bad in (None, {}, {"status": None}, {"status": "evidence_supported"}, {"status": "qualified"}, "within"):
            ds = rules.derive_decision_support(bad)
            self.assertEqual(ds["status"], "requires_validation", bad)
            self.assertIsNone(ds["domain_status"])
        # a decision dict still carrying the retired value (an old client, an old export) is NOT accepted as a
        # supported tier: the candidate gets no position, exactly like any other unrecognised status
        legacy = {"status": "evidence_supported", "domain_status": rules.DOMAIN_WITHIN}
        self.assertIsNone(ranking_rules._tier(legacy))
        result = ranking_rules.evidence_ranking(
            [{"id": 1, "predicted_probability": 0.99, "predicted_value": 50.0, "decision_support": legacy}], "maximize")
        self.assertIsNone(result["by_id"][1]["rank"])
        self.assertEqual(result["by_id"][1]["status"], ranking_rules.STATUS_NOT_RANKED)

    def test_ranked_statement_does_not_present_a_position_as_accuracy(self):
        text = ranking_rules.STATEMENTS[ranking_rules.STATUS_RANKED]
        self.assertIn("one feature at a time", text)
        self.assertIn("does not show the model is accurate for this candidate", text)
        self.assertIn("physical validation is still required", text)
        for retired in RETIRED:
            self.assertNotIn(retired, text)


class TestStatusThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_decision_semantics"

    def _ranked_case(self, label, candidates):
        org_id, client, case_id = self.setup_case(label)
        self.paid(org_id)
        up = self.upload(client, case_id, "history.csv", F.csv_bytes(F.CANON_HEADERS, HISTORY))
        self.assertEqual(up.status_code, 201, up.text)
        ids = {}
        for name, features in candidates:
            r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": features})
            self.assertEqual(r.status_code, 201, r.text)
            ids[name] = r.json()["id"]
        rk = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(rk.status_code, 200, rk.text)
        return org_id, client, case_id, ids, {x["candidate_name"]: x for x in rk.json()}

    def _listing(self, client, case_id):
        return {c["candidate_name"]: c for c in client.get(f"/api/change-cases/{case_id}/candidates").json()}

    def test_high_probability_in_range_candidate_is_not_labelled_validated_or_qualified(self):
        org_id, client, case_id, ids, ranked = self._ranked_case("Hi", [("Near", IN_RANGE), ("Near2", IN_RANGE_2)])
        for name, row in ranked.items():
            ds = row["decision_support"]
            self.assertEqual(ds["status"], "within_data_range", name)
            self.assertEqual(ds["label"], "Within data range")
            self.assertIn("qualification", ds["not_established"])
            self.assertIn("physical_performance", ds["not_established"])
        # the model's own numbers are unchanged and still labelled as an estimate
        self.assertTrue(any(r["predicted_probability"] >= 0.5 for r in ranked.values()),
                        "fixture should include a candidate the model rates as likely, so the test means something")
        body = json.dumps([ranked, self._listing(client, case_id)])
        for retired in RETIRED:
            self.assertNotIn(retired, body)

    def test_ranking_response_and_listing_agree_on_the_same_status(self):
        org_id, client, case_id, ids, ranked = self._ranked_case("Agree", [("A", IN_RANGE), ("Far", FAR)])
        listing = self._listing(client, case_id)
        for name in ranked:
            self.assertEqual(ranked[name]["decision_support"], listing[name]["decision_support"], name)
        self.assertEqual(listing["A"]["decision_support"]["status"], "within_data_range")
        self.assertEqual(listing["Far"]["decision_support"]["status"], "requires_validation")

    def test_status_is_derived_at_read_time_and_never_stored(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, ids, ranked = self._ranked_case("NoStore", [("A", IN_RANGE)])
        stored = candidate_repo.get_latest_prediction_for_candidate(org_id, ids["A"])
        self.assertIsNotNone(stored)
        self.assertNotIn("decision_support", stored)
        self.assertFalse([k for k, v in stored.items() if isinstance(v, str) and any(t in v for t in RETIRED)],
                         "no stored prediction field may carry the retired status")

    def test_a_recorded_pass_does_not_change_the_status_and_is_shown_as_customer_entered(self):
        org_id, client, case_id, ids, ranked = self._ranked_case("Out", [("A", IN_RANGE), ("B", IN_RANGE_2)])
        before = self._listing(client, case_id)
        r = client.post(f"/api/change-cases/{case_id}/candidates/{ids['A']}/outcome",
                        json={"actual_result": "Passed salt spray, 520 h", "passed_spec": True})
        self.assertEqual(r.status_code, 201, r.text)
        after = self._listing(client, case_id)
        for name in ("A", "B"):
            self.assertEqual(after[name]["decision_support"], before[name]["decision_support"], name)
            self.assertEqual(after[name]["rank"], before[name]["rank"], name)
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertIn("Recorded as meeting requirement", text)
        self.assertIn("does not verify them against a test method, specimen record or the requirement", text)
        self.assertIn("This analysis does not qualify any candidate", text)

    def test_no_recorded_evidence_is_reported_as_none_not_as_compliance(self):
        org_id, client, case_id, ids, ranked = self._ranked_case("None", [("A", IN_RANGE)])
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertIn("No physical validation outcomes have been recorded for this change case.", text)
        self.assertNotIn("Recorded as meeting requirement", text)

    def test_report_uses_the_same_label_and_states_the_same_limits(self):
        org_id, client, case_id, ids, ranked = self._ranked_case("Rep", [("A", IN_RANGE), ("Far", FAR)])
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertIn(ranked["A"]["decision_support"]["label"], text)           # same label as the API
        self.assertIn("checked one feature at a time", text)
        self.assertIn("does not show that the combination of inputs is covered by the historical data", text)
        self.assertIn("is not physical evidence", text)
        self.assertIn(report_builder.CROSS_VALIDATION_CAVEAT, text)
        for retired in RETIRED:
            self.assertNotIn(retired, text)

    def test_candidate_outside_the_range_is_still_not_ranked_and_not_called_unsuitable(self):
        org_id, client, case_id, ids, ranked = self._ranked_case("Far", [("A", IN_RANGE), ("Far", FAR)])
        self.assertIsNone(ranked["Far"]["rank"])
        self.assertEqual(ranked["Far"]["decision_support"]["status"], "requires_validation")
        self.assertIn("does not mean the candidate is unsuitable", ranked["Far"]["ranking_statement"])
        self.assertIsNotNone(ranked["A"]["rank"])


class TestFrontendUsesTheCurrentStatusNames(unittest.TestCase):
    FILES = ("lib/api/change-cases.ts", "app/change-cases/[id]/page.tsx")

    def test_no_frontend_source_still_uses_the_retired_names(self):
        if not FRONTEND.is_dir():
            self.skipTest("frontend sources are not part of this checkout")
        for rel in self.FILES:
            text = (FRONTEND / rel).read_text(encoding="utf-8")
            for retired in RETIRED:
                self.assertNotIn(retired, text, f"{rel} still mentions {retired!r}")
        types = (FRONTEND / "lib/api/change-cases.ts").read_text(encoding="utf-8")
        self.assertIn('"within_data_range" | "caution" | "requires_validation"', types)
        self.assertIn("not_established", types)


if __name__ == "__main__":
    unittest.main()
