"""Level 3 review fixes: the plan table shows the model's actual ranks (never row numbers), cites each repeated
measurement once using the SAME deduplication as the model fit, and flags customer-entered outcomes only for the
exact candidate of the exact change case. Run:  python3 -m unittest backend.tests.test_validation_plan_review -v"""
import io
import itertools
import unittest
from unittest import mock

from docx import Document

from backend.app.services import change_case_rules
from backend.app.services import report_builder as rb
from backend.app.services import validation_plan as vp
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase
from backend.tests.test_validation_plan import OUT_COV, _plan_table, _report, _result

NOT_RANKED = "not_ranked_insufficient_evidence"
RANKED, CAUTION = "ranked", "ranked_with_caution"


def _unranked(name):
    return _result(name, None, NOT_RANKED, p=0.99, value=120.0, cov=OUT_COV)


MIXED = [
    _result("B", 5, RANKED), _result("A", 2, RANKED), _result("C", 7, CAUTION),
    _unranked("U1"), _unranked("U2"),
]


class TestRankColumnShowsActualRanks(unittest.TestCase):
    def test_cells_are_the_model_ranks_not_row_numbers(self):
        rows = _plan_table(_report(MIXED)[1])
        self.assertEqual([r[1] for r in rows[1:]], ["A", "B", "C", "U1", "U2"])
        self.assertEqual([r[0] for r in rows[1:]], ["2", "5", "7 (provisional)", "No rank", "No rank"])
        shown = {r[0] for r in rows[1:]}
        for row_number in ("1", "3", "4"):
            self.assertNotIn(row_number, shown)

    def test_an_unranked_candidate_never_gets_a_number(self):
        rows = _plan_table(_report([_result("A", 1, RANKED), _result("B", 2, RANKED), _unranked("U")])[1])
        self.assertEqual([r[0] for r in rows[1:]], ["1", "2", "No rank"])  # the old row counter would show "3"

    def test_order_does_not_depend_on_the_callers_order(self):
        reference = _plan_table(_report(MIXED)[1])
        supported = [r for r in MIXED if r["rank"] is not None]
        unranked = [r for r in MIXED if r["rank"] is None]
        for perm in itertools.permutations(supported):
            self.assertEqual(_plan_table(_report(list(perm) + unranked)[1]), reference, [p["candidate_name"] for p in perm])

    def test_unranked_candidates_keep_the_given_order(self):
        rows = _plan_table(_report([_result("A", 1, RANKED), _unranked("U2"), _unranked("U1")])[1])
        self.assertEqual([r[1] for r in rows[1:]], ["A", "U2", "U1"])

    def test_provisional_label_only_on_the_caution_tier(self):
        self.assertEqual(rb._rank_cell(_result("x", 3, RANKED)), "3")
        self.assertEqual(rb._rank_cell(_result("x", 3, CAUTION)), "3 (provisional)")
        self.assertEqual(rb._rank_cell(_unranked("x")), "No rank")
        self.assertEqual(rb._rank_cell({"rank": None, "ranking_status": CAUTION}), "No rank")

    def test_a_supported_result_without_a_rank_says_so_instead_of_rank_none(self):
        legacy = _result("Legacy", None, None)
        paragraphs, tables = _report([_result("A", 1, RANKED), _unranked("U"), legacy])
        rows = _plan_table(tables)
        self.assertEqual([r[1] for r in rows[1:]], ["A", "Legacy", "U"])  # after ranked, before unranked
        self.assertEqual(rows[2][0], "No rank")
        self.assertTrue(rows[2][2].startswith("No rank recorded:"))
        everything = "\n".join(paragraphs) + "\n".join(c for t in tables for row in t for c in row)
        self.assertNotIn("Rank None", everything)

    def test_only_unranked_candidates_still_get_a_plan(self):
        rows = _plan_table(_report([_unranked("U1"), _unranked("U2")])[1])
        self.assertEqual([r[0] for r in rows[1:]], ["No rank", "No rank"])
        self.assertTrue(all(r[2].startswith("No rank: its inputs are outside the historical evidence") for r in rows[1:]))

    def test_headers_describe_what_the_columns_hold(self):
        paragraphs, tables = _report(MIXED)
        header = _plan_table(tables)[0]
        self.assertEqual(header[0], "Model rank")
        self.assertEqual(header[-1], "Outcome recorded (customer-entered)")
        everything = "\n".join(paragraphs) + "\n".join(c for t in tables for row in t for c in row)
        self.assertNotIn("Physical result recorded", everything)
        order = next(p for p in paragraphs if p.startswith("Order of testing:"))
        self.assertIn("outside the evidence, which have no rank.", order)
        self.assertNotIn("which have no position", order)

    def test_the_other_statements_are_unchanged(self):
        text = "\n".join(_report(MIXED)[0])
        self.assertIn(vp.NOT_SPECIFIED_BY_ANALYSIS, text)
        self.assertIn("not an experimental design chosen to reduce uncertainty", text)
        self.assertIn("How to read a physical result", text)
        self.assertIn("does not verify recorded results", text)


def _row(i, a, b, target):
    return {"id": i, "features": {"a": a, "b": b}, "target_value": target, "provenance": None}


class TestRepeatedMeasurementsAreCitedOnce(unittest.TestCase):
    def test_training_deduplication_keeps_the_first_id_and_only_exact_repeats_collapse(self):
        rows = [_row(1, 0.0, 0.0, 10.0), _row(2, 0.0, 0.0, 10.0), _row(3, 0.0, 0.0, 10.5),  # 3: replicate, differs
                _row(4, 1.0, 1.0, 20.0), _row(5, 1.0, 1.0, 20.0)]
        kept = change_case_rules.deduplicate_rows(rows)
        self.assertEqual([r["id"] for r in kept], [1, 3, 4])
        near = vp.closest_records({"a": 0.0, "b": 0.0}, kept, ["a", "b"], k=5)
        self.assertEqual([c["reference"] for c in near], ["evidence record 1", "evidence record 3", "evidence record 4"])
        raw = vp.closest_records({"a": 0.0, "b": 0.0}, rows, ["a", "b"], k=5)
        self.assertIn("evidence record 2", [c["reference"] for c in raw], "premise: without it the repeat is cited")


class TestPlanThroughTheApplication(IsolatedApiTestCase):
    dir_name = "_test_env_validation_plan_review"
    INA = {"crosslinker_ratio": 0.12, "cure_temp_c": 158}

    def _plan(self, client, case_id):
        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        doc = Document(io.BytesIO(r.content))
        return _plan_table([[[c.text for c in row.cells] for row in t.rows] for t in doc.tables])

    def _cited_ids(self, plan):
        return [int(line.split("evidence record ")[1].split()[0].rstrip("(:"))
                for row in plan[1:] for line in row[4].splitlines() if "evidence record" in line]

    def _add(self, client, case_id, name, features=None):
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": features or self.INA})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def test_each_repeated_measurement_is_cited_once_with_the_training_deduplication(self):
        org_id, client, case_id = self.setup_case("DedupE2E")
        self.paid(org_id)
        rows = F.base_rows(14)
        self.assertEqual(self.upload(client, case_id, "dup.csv", F.csv_bytes(F.CANON_HEADERS, rows + rows)).status_code, 201)
        stored = self.experiments(org_id, self.datasets(org_id, case_id)[0]["id"])
        self.assertEqual(len(stored), 28)
        training_rows = [{"id": e["id"], "features": __import__("json").loads(e["features_json"]),
                          "target_value": e["target_value"]} for e in stored]
        kept_ids = {r["id"] for r in change_case_rules.deduplicate_rows(training_rows)}
        self.assertEqual(len(kept_ids), 14)
        self._add(client, case_id, "InA")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        real = change_case_rules.deduplicate_rows
        with mock.patch.object(change_case_rules, "deduplicate_rows", wraps=real) as spy:
            plan = self._plan(client, case_id)
        cited = self._cited_ids(plan)
        self.assertEqual(len(cited), 3)
        self.assertEqual(len(set(cited)), 3)
        self.assertTrue(set(cited) <= kept_ids, f"cited {cited}, training keeps {sorted(kept_ids)}")
        history_calls = [c for c in spy.call_args_list if c.args and c.args[0] and "id" in c.args[0][0]]
        self.assertTrue(history_calls, "the report must call the training deduplication on the evidence rows")
        self.assertEqual(len(history_calls[0].args[0]), 28)
        values = [line for line in plan[1][4].splitlines()]
        self.assertEqual(len(values), len(set(values)))

    def test_cited_records_come_from_the_dataset_the_predictions_used(self):
        org_id, client, case_id = self.setup_case("DatasetE2E")
        self.paid(org_id)
        rows = F.base_rows(14)
        self.assertEqual(self.upload(client, case_id, "first.csv", F.csv_bytes(F.CANON_HEADERS, rows)).status_code, 201)
        self._add(client, case_id, "InA")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        lower = [(a, b, c * 0.5) for a, b, c in rows]
        self.assertEqual(self.upload(client, case_id, "second.csv", F.csv_bytes(F.CANON_HEADERS, lower)).status_code, 201)
        datasets = sorted(self.datasets(org_id, case_id), key=lambda d: d["id"])
        first = {e["id"] for e in self.experiments(org_id, datasets[0]["id"])}
        second = {e["id"] for e in self.experiments(org_id, datasets[1]["id"])}
        self.assertTrue(first.isdisjoint(second))
        cited = self._cited_ids(self._plan(client, case_id))  # not re-ranked: still the first dataset's predictions
        self.assertEqual(len(cited), 3)
        self.assertTrue(set(cited) <= first and not set(cited) & second, (cited, sorted(first)[:3], sorted(second)[:3]))

    def _case_with_shared_candidate(self, client, org_id, label_rows=None):
        case_id = self.make_case(client, self._spec)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14))).status_code, 201)
        cid = self._add(client, case_id, "Shared")
        self._add(client, case_id, "Other", {"crosslinker_ratio": 0.10, "cure_temp_c": 150})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        return case_id, cid

    def _flags(self, client, case_id):
        return {row[1]: row[5] for row in self._plan(client, case_id)[1:]}

    def _record_outcome(self, client, case_id, cid):
        r = client.post(f"/api/change-cases/{case_id}/candidates/{cid}/outcome",
                        json={"actual_result": "310 h", "passed_spec": True})
        self.assertEqual(r.status_code, 201, r.text)

    def test_an_outcome_is_flagged_only_for_its_own_candidate_in_its_own_change_case(self):
        from backend.tests._api_base import SPEC
        self._spec = SPEC
        org_a, client_a = self.org_and_client("OutcomeA")
        self.paid(org_a)
        case_1, cid_1 = self._case_with_shared_candidate(client_a, org_a)
        case_2, cid_2 = self._case_with_shared_candidate(client_a, org_a)   # same organisation, same candidate name
        self.assertNotEqual(cid_1, cid_2)
        self.assertEqual(self._flags(client_a, case_1), {"Shared": "No", "Other": "No"})
        self._record_outcome(client_a, case_1, cid_1)
        self.assertEqual(self._flags(client_a, case_1), {"Shared": "Yes (see recorded outcomes)", "Other": "No"})
        self.assertEqual(self._flags(client_a, case_2), {"Shared": "No", "Other": "No"}, "another change case must not inherit it")

        org_b, client_b = self.org_and_client("OutcomeB")                      # another organisation
        self.paid(org_b)
        case_b, cid_b = self._case_with_shared_candidate(client_b, org_b)
        self._record_outcome(client_b, case_b, cid_b)
        self.assertEqual(self._flags(client_b, case_b)["Shared"], "Yes (see recorded outcomes)")
        self.assertEqual(self._flags(client_a, case_2), {"Shared": "No", "Other": "No"}, "another organisation must not leak in")
        self.assertEqual(self._flags(client_a, case_1)["Other"], "No")


if __name__ == "__main__":
    unittest.main()
