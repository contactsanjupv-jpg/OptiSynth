"""Level 3: a prioritised, evidence-traceable physical-validation plan in the report.
Nothing here asserts a test method, specimen count or acceptance limit: the report must not state any.
Synthetic data proves the wiring and wording only. Run:  python3 -m unittest backend.tests.test_validation_plan -v"""
import io
import json
import os
import unittest

from docx import Document

from backend.app.services import change_case_service as svc
from backend.app.services import report_builder as rb
from backend.app.services import validation_plan as vp
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase

FEATS = ["a", "b"]


def _rec(i, a, b, target, prov=None):
    return {"id": i, "features": {"a": a, "b": b}, "target_value": target, "provenance": prov}


class TestClosestRecords(unittest.TestCase):
    RECS = [_rec(1, 0.0, 0.0, 10.0), _rec(2, 1.0, 1.0, 20.0), _rec(3, 0.5, 0.5, 15.0), _rec(4, 0.1, 0.0, 11.0)]

    def test_nearest_first_limited_to_k_with_stored_values(self):
        out = vp.closest_records({"a": 0.05, "b": 0.0}, self.RECS, FEATS, k=2)
        self.assertEqual([o["reference"] for o in out], ["evidence record 1", "evidence record 4"])
        self.assertEqual([o["target"] for o in out], [10.0, 11.0])
        self.assertLessEqual(out[0]["distance"], out[1]["distance"])

    def test_default_shows_three_and_ties_break_on_lower_id(self):
        recs = [_rec(9, 1.0, 0.0, 1.0), _rec(5, 0.0, 1.0, 2.0), _rec(7, 0.0, 0.0, 3.0), _rec(2, 1.0, 1.0, 4.0)]
        out = vp.closest_records({"a": 0.5, "b": 0.5}, recs, FEATS)
        self.assertEqual(len(out), 3)
        self.assertEqual([o["reference"] for o in out], ["evidence record 2", "evidence record 5", "evidence record 7"])

    def test_features_are_scaled_by_their_historical_range(self):
        recs = [{"id": 1, "features": {"a": 0.0, "b": 0.0}, "target_value": 1.0, "provenance": None},
                {"id": 2, "features": {"a": 0.001, "b": 1000.0}, "target_value": 2.0, "provenance": None}]
        out = vp.closest_records({"a": 0.0005, "b": 10.0}, recs, FEATS, k=1)
        self.assertEqual(out[0]["reference"], "evidence record 1")  # 1% of b's range beats 50% of a's range

    def test_constant_column_contributes_nothing(self):
        recs = [{"id": i, "features": {"a": float(i), "b": 5.0}, "target_value": float(i), "provenance": None} for i in range(4)]
        out = vp.closest_records({"a": 1.0, "b": 99.0}, recs, FEATS, k=1)
        self.assertEqual(out[0]["reference"], "evidence record 1")

    def test_unusable_inputs_give_nothing_never_a_guess(self):
        self.assertEqual(vp.closest_records({"a": 1.0, "b": 1.0}, [], FEATS), [])
        self.assertEqual(vp.closest_records({"a": 1.0}, self.RECS, FEATS), [])            # candidate lacks b
        self.assertEqual(vp.closest_records({"a": 1.0, "b": True}, self.RECS, FEATS), [])  # bool is not a value
        self.assertEqual(vp.closest_records({"a": 1.0, "b": 1.0}, self.RECS, FEATS, k=0), [])
        partial = [_rec(1, 0.0, 0.0, 1.0), {"id": 2, "features": {"a": 1.0}, "target_value": 2.0, "provenance": None}]
        self.assertEqual([o["reference"] for o in vp.closest_records({"a": 1.0, "b": 1.0}, partial, FEATS)],
                         ["evidence record 1"])

    def test_provenance_is_quoted_when_kept(self):
        self.assertEqual(vp.record_reference(12, {"location": "history.csv", "row": 7}),
                         "evidence record 12 (history.csv row 7)")
        self.assertEqual(vp.record_reference(12, {"table": "Table 2", "row": 3}), "evidence record 12 (Table 2 row 3)")
        self.assertEqual(vp.record_reference(12, {"file": "x.pdf"}), "evidence record 12 (x.pdf)")
        self.assertEqual(vp.record_reference(12, None), "evidence record 12")
        self.assertEqual(vp.record_reference(12, {"row": 7}), "evidence record 12")


class TestGapsAndConditions(unittest.TestCase):
    LABELS = rb.DOMAIN_COVERAGE_LABELS

    def test_gaps_name_the_feature_value_and_observed_range(self):
        cov = {"status": "outside_historical_domain", "features": {
            "a": {"status": "within_historical_domain", "value": 1, "historical_min": 0, "historical_max": 2},
            "b": {"status": "outside_historical_domain", "value": 9.5, "historical_min": 1, "historical_max": 4}}}
        gaps = vp.candidate_gaps(cov, self.LABELS)
        self.assertEqual(gaps, ["b = 9.5 is outside historical range (observed 1 to 4)"])
        self.assertEqual(vp.candidate_gaps(None, self.LABELS), [])

    def test_reference_condition_three_cases(self):
        both = vp.reference_condition_text({"condition_columns": ["curing_days"],
                                            "conditions": {"reference": {"curing_days": 28}}})
        self.assertIn("curing_days = 28", both)
        self.assertIn("only under the same conditions", both)
        self.assertIn("no single reference condition set", vp.reference_condition_text(
            {"condition_columns": ["curing_days"], "conditions": {}}))
        for none in (None, {}, {"condition_columns": []}):
            self.assertIn("No test conditions were declared", vp.reference_condition_text(none))


class TestStoredExperimentText(unittest.TestCase):
    def _text(self, p, **kw):
        return svc._minimum_experiment_description({"name": "C", "predicted_probability": p}, 35.0, "maximize", **kw)

    def test_position_is_stated_and_no_candidate_is_told_it_is_the_one_to_test_first(self):
        for p in (0.05, 0.5, 0.95):
            self.assertNotIn("identifies which candidate to test first", self._text(p))
            self.assertNotIn("identifies which candidate to test first", self._text(p, position=(3, 3)))
        self.assertIn("Suggested test position 2 of 3 among the candidates the evidence supports.",
                      self._text(0.5, position=(2, 3)))
        self.assertNotIn("Suggested test position", self._text(0.5))
        self.assertIn("does not replace running and recording the actual physical result", self._text(0.5, position=(1, 1)))

    def test_candidates_outside_the_evidence_get_no_position(self):
        t = self._text(0.9, ranking_status="not_ranked_insufficient_evidence", position=None)
        self.assertNotIn("Suggested test position", t)
        self.assertIn("cannot say whether it is likely to meet the requirement", t)


def _result(name, rank, status, p=0.8, value=40.0, sd=1.5, cov=None, closest=None, outcome=False):
    return {"rank": rank, "ranking_status": status, "candidate_name": name, "predicted_value": value,
            "predicted_probability": p, "uncertainty_std": sd, "recommended_experiment": f"Run {name}.",
            "domain_coverage": cov, "decision_support": None, "closest_records": closest or [],
            "outcome_recorded": outcome}


SPEC = {"feature_columns": FEATS, "target_metric": "strength", "target_value": 35.0, "direction": "maximize",
        "units": {"a": "kg", "b": "kg", "strength": "MPa"}}
OUT_COV = {"status": "outside_historical_domain", "note": "Outside the observed range.", "features": {
    "a": {"status": "outside_historical_domain", "value": 9.0, "historical_min": 1.0, "historical_max": 4.0},
    "b": {"status": "within_historical_domain", "value": 2.0, "historical_min": 1.0, "historical_max": 4.0}}}


def _report(results, spec=SPEC, review=None):
    ctx = None if spec is None else {"change_case_id": 3, "spec": spec, "generated_at_utc": "2026-11-01 00:00:00",
                                      "dataset": None, "ingestion_review": review, "candidates": [], "outcomes": []}
    path = rb.build_qualification_report({"name": "Case", "trigger_type": "regulatory_restriction"}, results, 20, "Acme",
                                         audit_context=ctx)
    doc = Document(path)
    os.remove(path)
    paragraphs = [p.text for p in doc.paragraphs]
    tables = [[[c.text for c in row.cells] for row in t.rows] for t in doc.tables]
    return paragraphs, tables


def _plan_table(tables):
    return next(t for t in tables if t[0][0] == "Model rank")


class TestReportPlanSection(unittest.TestCase):
    RESULTS = [
        _result("Alpha", 1, "ranked", p=0.9, closest=[{"reference": "evidence record 4 (h.csv row 6)", "target": 41.0, "distance": 0.12}]),
        _result("Beta", 2, "ranked_with_caution", p=0.5, value=36.0),
        _result("Gamma", None, "not_ranked_insufficient_evidence", p=0.99, value=120.0, cov=OUT_COV, outcome=True),
    ]
    REVIEW = {"condition_columns": ["curing_days"], "conditions": {"reference": {"curing_days": 28}}}

    def test_requirement_conditions_and_what_is_not_specified_are_stated_once(self):
        paragraphs, _ = _report(self.RESULTS, review=self.REVIEW)
        text = "\n".join(paragraphs)
        self.assertEqual(text.count("What a physical test must establish: strength (MPa) at or above 35.0."), 1)
        self.assertIn("only acceptance criterion the system holds", text)
        self.assertIn(vp.NOT_SPECIFIED_BY_ANALYSIS, text)
        self.assertIn("curing_days = 28", text)
        self.assertIn("not an experimental design chosen to reduce uncertainty", text)

    def test_plan_table_order_basis_gaps_records_and_outcome_flag(self):
        _, tables = _report(self.RESULTS, review=self.REVIEW)
        rows = _plan_table(tables)
        self.assertEqual([r[1] for r in rows[1:]], ["Alpha", "Beta", "Gamma"])
        self.assertEqual([r[0] for r in rows[1:]], ["1", "2 (provisional)", "No rank"])
        self.assertIn("Rank 1: model-estimated probability 90%", rows[1][2])
        self.assertIn("predicted strength (MPa) 40", rows[1][2])
        self.assertIn("Ranked provisionally", rows[2][2])
        self.assertTrue(rows[3][2].startswith("No rank: its inputs are outside the historical evidence"))
        self.assertIn("a = 9.0 is outside historical range (observed 1.0 to 4.0)", rows[3][3])
        self.assertIn("None found by the per-feature range check (joint coverage is not checked).", rows[1][3])
        self.assertEqual(rows[1][4], "evidence record 4 (h.csv row 6): 41.0 (distance 0.12)")
        self.assertEqual(rows[2][4], "Not available.")
        self.assertEqual([r[5] for r in rows[1:]], ["No", "No", "Yes (see recorded outcomes)"])

    def test_how_to_read_a_result_uses_only_the_spec_threshold(self):
        paragraphs, _ = _report(self.RESULTS)
        text = "\n".join(paragraphs)
        self.assertIn("How to read a physical result", paragraphs)
        self.assertIn("A measured strength (MPa) at or above 35.0", text)
        self.assertIn("It does not qualify it", text)
        self.assertIn("contradicts the model's estimate for this requirement", text)
        self.assertIn("much more than the predicted standard deviation", text)

    def test_no_invented_procedure_sample_count_or_limit(self):
        text = "\n".join(_report(self.RESULTS, review=self.REVIEW)[0])
        for invented in ("ASTM", "ISO ", "EN ", "GB/T", "specimens per", "at least 3", "triplicate", "three specimens",
                         "95% confidence", "pass criterion"):
            self.assertNotIn(invented, text, invented)

    def test_minimum_direction_and_missing_inputs_degrade_honestly(self):
        spec = dict(SPEC, direction="minimize")
        paragraphs, _ = _report(self.RESULTS[:1], spec=spec)
        self.assertIn("strength (MPa) at or below 35.0", "\n".join(paragraphs))
        paragraphs, tables = _report(self.RESULTS, spec=None)  # no audit context at all
        self.assertNotIn("What a physical test must establish", "\n".join(paragraphs))
        self.assertEqual(len(_plan_table(tables)), 4)
        r = _result("Old", 1, "ranked", value=None)  # prediction stored before values were recorded
        del r["closest_records"], r["outcome_recorded"]
        _, tables = _report([r])
        row = _plan_table(tables)[1]
        self.assertIn("predicted value not recorded", row[2])
        self.assertEqual(row[4], "Not available.")
        self.assertEqual(row[5], "No")

    def test_existing_section_contract_is_unchanged(self):
        paragraphs, _ = _report(self.RESULTS)
        self.assertIn("Recommended Validation Experiments", paragraphs)
        self.assertIn("Run Alpha.", "\n".join(paragraphs).replace("Alpha: ", ""))


class TestPlanThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_validation_plan"

    def _report_doc(self, client, case_id):
        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        doc = Document(io.BytesIO(r.content))
        return [p.text for p in doc.paragraphs], [[[c.text for c in row.cells] for row in t.rows] for t in doc.tables]

    def test_plan_rows_are_in_ranking_order_and_cite_real_evidence_records(self):
        from backend.app.repositories import qualification_dataset_repo as repo
        org_id, client, case_id = self.setup_case("PlanE2E")
        self.paid(org_id)
        history = F.base_rows(14)
        self.assertEqual(self.upload(client, case_id, "history.csv", F.csv_bytes(F.CANON_HEADERS, history)).status_code, 201)
        for name, feats in (("InA", {"crosslinker_ratio": 0.12, "cure_temp_c": 158}),
                            ("InB", {"crosslinker_ratio": 0.10, "cure_temp_c": 150}),
                            ("Far", {"crosslinker_ratio": 0.40, "cure_temp_c": 300})):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/candidates",
                                         json={"name": name, "features": feats}).status_code, 201)
        ranked = client.post(f"/api/change-cases/{case_id}/rank").json()
        paragraphs, tables = self._report_doc(client, case_id)
        plan = _plan_table(tables)
        expected = [r["candidate_name"] for r in sorted(ranked, key=lambda r: (r["rank"] is None, r["rank"] or 0))]
        self.assertEqual([r[1] for r in plan[1:]], expected)
        self.assertEqual(plan[-1][1], "Far")
        self.assertTrue(plan[-1][2].startswith("No rank"))
        self.assertIn("outside historical range", plan[-1][3])
        # every cited record is a real stored row with the quoted measurement
        dataset = repo.get_latest_dataset_for_change_case(org_id, case_id)
        stored = {e["id"]: e["target_value"] for e in repo.list_experiments_for_dataset(org_id, dataset["id"])}
        self.assertEqual(len(stored), 14)
        checked = 0
        for row in plan[1:]:
            for line in row[4].splitlines():
                rid = int(line.split("evidence record ")[1].split()[0].rstrip("(:"))
                quoted = float(line.split(": ")[1].split()[0])
                self.assertEqual(stored[rid], quoted)
                checked += 1
        self.assertGreaterEqual(checked, 3)
        text = "\n".join(paragraphs)
        self.assertIn("How to read a physical result", paragraphs)
        self.assertIn("Suggested test position", text)
        self.assertNotIn("identifies which candidate to test first", text)

    def test_recorded_outcome_is_flagged_in_the_plan(self):
        org_id, client, case_id = self.setup_case("PlanOutcome")
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        cid = client.post(f"/api/change-cases/{case_id}/candidates",
                          json={"name": "Tested", "features": {"crosslinker_ratio": 0.12, "cure_temp_c": 158}}).json()["id"]
        client.post(f"/api/change-cases/{case_id}/candidates",
                    json={"name": "Untested", "features": {"crosslinker_ratio": 0.10, "cure_temp_c": 150}})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        r = client.post(f"/api/change-cases/{case_id}/candidates/{cid}/outcome",
                        json={"actual_result": "310 h", "passed_spec": True})
        self.assertIn(r.status_code, (200, 201), r.text)
        _, tables = self._report_doc(client, case_id)
        flags = {row[1]: row[5] for row in _plan_table(tables)[1:]}
        self.assertEqual(flags["Tested"], "Yes (see recorded outcomes)")
        self.assertEqual(flags["Untested"], "No")


if __name__ == "__main__":
    unittest.main()
