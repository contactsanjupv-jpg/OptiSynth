"""
Level 1D: candidate editing, removal and deletion, stale-ranking handling, strict candidate input
validation, and the migration that supports them.

What is proven here, through the real HTTP path unless a test says otherwise:
  * a candidate's name and input values can be edited and persist; every check an ADD applies is
    applied to an EDIT, and values for fields the diagnostic does not model are refused (they used to be
    stored silently), as are booleans and numeric strings;
  * editing input values keeps the old prediction as history but marks it as computed for the previous
    values -- rank, domain coverage and decision support are withheld, and the report is refused --
    until the ranking is re-run;
  * adding, removing or restoring a candidate after a ranking withholds the order the same way;
  * a candidate nothing depends on can be deleted; a ranked one is "removed from the analysis" instead
    (kept, restorable, excluded from ranking); a recorded physical outcome is never deleted and locks the
    candidate's input values;
  * a completed or archived diagnostic refuses every candidate change; other organizations get 404;
  * the migration adds only nullable / defaulted columns and survives downgrade and re-upgrade.

Run:  python3 -m unittest backend.tests.test_candidate_lifecycle -v
"""
import json
import os
import shutil
import unittest

from backend.app.services import candidate_lifecycle_rules as rules
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase

A_FEATURES = {"crosslinker_ratio": 0.145, "cure_temp_c": 170}
# Inside the synthetic history's observed range (crosslinker 0.080-0.145, cure temp 140-179 C). It used to be
# (0.16, 178), which is OUTSIDE that range; candidates outside it are no longer given a rank (see
# test_domain_aware_ranking.py), and these tests are about editing / removing candidates, not about that.
B_FEATURES = {"crosslinker_ratio": 0.13, "cure_temp_c": 165}
A_EDITED = {"crosslinker_ratio": 0.10, "cure_temp_c": 150}


class TestCandidateRulesPure(unittest.TestCase):
    T0, T1, T2 = "2026-10-18T09:00:00+00:00", "2026-10-18T09:05:00+00:00", "2026-10-18T09:10:00+00:00"

    def test_ranking_set_changed_scenarios(self):
        ranked = lambda t, restored=None: {"latest_prediction_created_at": t, "restored_at": restored}
        # nothing ranked yet -> nothing to be out of date
        self.assertFalse(rules.ranking_set_changed([ranked(None)], []))
        # everyone ranked, nobody removed -> current
        self.assertFalse(rules.ranking_set_changed([ranked(self.T1), ranked(self.T1)], []))
        # (a) a candidate added after the run
        self.assertTrue(rules.ranking_set_changed([ranked(self.T1), ranked(None)], []))
        # (b) a candidate restored after the run
        self.assertTrue(rules.ranking_set_changed([ranked(self.T1), ranked(self.T0, restored=self.T2)], []))
        # restored BEFORE the run -> the run covered it
        self.assertFalse(rules.ranking_set_changed([ranked(self.T1, restored=self.T0)], []))
        # (c) a ranked candidate removed after the run / before the run
        self.assertTrue(rules.ranking_set_changed(
            [ranked(self.T1)], [{"archived_at": self.T2, "prediction_count": 1}]))
        self.assertFalse(rules.ranking_set_changed(
            [ranked(self.T1)], [{"archived_at": self.T0, "prediction_count": 1}]))
        # a never-ranked candidate removed after the run changes nothing that was ranked
        self.assertFalse(rules.ranking_set_changed(
            [ranked(self.T1)], [{"archived_at": self.T2, "prediction_count": 0}]))
        # timestamps compare as times, not as text (no microseconds vs microseconds)
        self.assertTrue(rules.ranking_set_changed(
            [ranked("2026-10-18T09:00:00+00:00")],
            [{"archived_at": "2026-10-18T09:00:00.000001+00:00", "prediction_count": 1}]))

    def test_features_known_rejects_anything_the_model_does_not_read(self):
        rules.check_features_known(["a", "b"], {"a": 1, "b": 2})
        with self.assertRaises(Exception) as ctx:
            rules.check_features_known(["a", "b"], {"a": 1, "b": 2, "colour_index": 3}, "X")
        self.assertIn("colour_index", ctx.exception.message)
        with self.assertRaises(Exception):  # names are exact: a different case is a different field
            rules.check_features_known(["cure_temp_c"], {"Cure_Temp_C": 1})

    def test_lifecycle_flags(self):
        plain = rules.lifecycle_flags({}, {})
        self.assertTrue(plain["deletable"] and plain["features_editable"] and not plain["archived"])
        ranked = rules.lifecycle_flags({}, {"prediction_count": 2})
        self.assertFalse(ranked["deletable"])
        self.assertIn("remove it from the analysis", ranked["delete_blocked_reason"])
        self.assertTrue(ranked["features_editable"])  # a ranked candidate can still be corrected
        tested = rules.lifecycle_flags({}, {"prediction_count": 1, "outcome_count": 1})
        self.assertFalse(tested["deletable"] or tested["features_editable"])
        self.assertIn("physical validation outcome", tested["delete_blocked_reason"])
        self.assertTrue(rules.lifecycle_flags({"archived_at": "x"}, {})["archived"])

    def test_prediction_matches_inputs(self):
        self.assertIsNone(rules.prediction_matches_inputs({"inputs_version": 0}, None))
        self.assertTrue(rules.prediction_matches_inputs({"inputs_version": 2}, {"inputs_version": 2}))
        self.assertFalse(rules.prediction_matches_inputs({"inputs_version": 3}, {"inputs_version": 2}))
        # rows stored before the column existed read as version 0 on both sides
        self.assertTrue(rules.prediction_matches_inputs({"inputs_version": None}, {}))

    def test_check_can_edit(self):
        open_case = {"status": "draft"}
        live = {"archived_at": None}
        with self.assertRaises(Exception):
            rules.check_can_edit(open_case, live, {})
        with self.assertRaises(Exception):
            rules.check_can_edit(open_case, live, {"units": {"a": "h"}})  # units alone
        with self.assertRaises(Exception):
            rules.check_can_edit(open_case, live, {"candidate_name": "x"})  # not an editable field
        with self.assertRaises(Exception):
            rules.check_can_edit({"status": "completed"}, live, {"name": "x"})
        with self.assertRaises(Exception):
            rules.check_can_edit(open_case, {"archived_at": "t"}, {"name": "x"})
        rules.check_can_edit(open_case, live, {"name": "x"})
        rules.check_can_edit({"status": "active"}, live, {"features": {}, "units": {}})


class TestCandidateLifecycleApi(IsolatedApiTestCase):
    dir_name = "_test_env_cand_life"

    # ------------------------------------------------------------ helpers
    def _case(self, label):
        org_id, client, case_id = self.setup_case(label)
        self.paid(org_id)
        up = self.upload(client, case_id, "history.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(14)))
        self.assertEqual(up.status_code, 201, up.text)
        return org_id, client, case_id

    def _add(self, client, case_id, name, features):
        r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": features})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def _rank(self, client, case_id):
        r = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _list(self, client, case_id, archived=False):
        r = client.get(f"/api/change-cases/{case_id}/candidates" + ("?include_archived=true" if archived else ""))
        self.assertEqual(r.status_code, 200, r.text)
        return {c["candidate_name"]: c for c in r.json()}

    def _ranked_case(self, label):
        org_id, client, case_id = self._case(label)
        a = self._add(client, case_id, "PolyGuard", A_FEATURES)
        b = self._add(client, case_id, "EcoShield", B_FEATURES)
        self._rank(client, case_id)
        return org_id, client, case_id, a, b

    def _patch(self, client, case_id, cid, body):
        return client.patch(f"/api/change-cases/{case_id}/candidates/{cid}", json=body)

    def _raw(self, client, method, url, content):
        return client.request(method, url, content=content, headers={"content-type": "application/json"})

    # ------------------------------------------------------------ editing
    def test_edit_name_and_values_persist_and_are_returned_in_list_shape(self):
        _, client, case_id = self._case("edit")
        cid = self._add(client, case_id, "PolyGuard", A_FEATURES)
        r = self._patch(client, case_id, cid, {"name": "  PolyGuard 2  ", "features": A_EDITED})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["candidate_name"], "PolyGuard 2")
        self.assertEqual(body["properties"], {"crosslinker_ratio": 0.10, "cure_temp_c": 150.0})
        self.assertEqual(body["inputs_version"], 1)
        self.assertIn("deletable", body)
        again = self._list(client, case_id)["PolyGuard 2"]  # persisted, not just echoed
        self.assertEqual(again["properties"], body["properties"])
        self.assertNotIn("_changed", body)
        self.assertNotIn("_previous_features", body)

    def test_a_name_only_edit_does_not_touch_input_version_or_predictions(self):
        _, client, case_id, a, _b = self._ranked_case("renameonly")
        r = self._patch(client, case_id, a, {"name": "PolyGuard Renamed"})
        self.assertEqual(r.status_code, 200, r.text)
        view = self._list(client, case_id)["PolyGuard Renamed"]
        self.assertEqual(view["inputs_version"], 0)
        self.assertFalse(view["prediction_inputs_stale"])
        self.assertIsNotNone(view["rank"])
        # changing only the capitalisation is a rename of the same candidate, not a duplicate
        self.assertEqual(self._patch(client, case_id, a, {"name": "POLYGUARD RENAMED"}).status_code, 200)

    def test_edit_applies_every_check_an_add_applies(self):
        _, client, case_id = self._case("editvalid")
        cid = self._add(client, case_id, "PolyGuard", A_FEATURES)
        other = self._add(client, case_id, "EcoShield", B_FEATURES)
        bad = {
            "missing feature": {"features": {"crosslinker_ratio": 0.1}},
            "unknown feature": {"features": {**A_EDITED, "colour_index": 4}},
            "wrong-case feature name": {"features": {"crosslinker_ratio": 0.1, "Cure_Temp_C": 150}},
            "duplicate name": {"name": " ecoshield "},
            "blank name": {"name": "   "},
            "nothing sent": {},
            "units without values": {"units": {"cure_temp_c": "degC"}},
            "no-op": {"name": "PolyGuard", "features": A_FEATURES},
        }
        for label, body in bad.items():
            r = self._patch(client, case_id, cid, body)
            self.assertEqual(r.status_code, 400, f"{label}: {r.text}")
        shape_errors = {
            "boolean value": {"features": {"crosslinker_ratio": True, "cure_temp_c": 150}},
            "numeric text": {"features": {"crosslinker_ratio": "0.1", "cure_temp_c": 150}},
            "null name": {"name": None},
            "null features": {"features": None},
            "unknown field": {"candidate_name": "x"},
            "unknown field with a valid one": {"name": "ok", "properties": {}},
        }
        for label, body in shape_errors.items():
            r = self._patch(client, case_id, cid, body)
            self.assertEqual(r.status_code, 422, f"{label}: {r.text}")
        for token in ("NaN", "Infinity", "-Infinity"):  # accepted by the JSON decoder, refused by the check
            r = self._raw(client, "PATCH", f"/api/change-cases/{case_id}/candidates/{cid}",
                          '{"features": {"crosslinker_ratio": %s, "cure_temp_c": 150}}' % token)
            self.assertEqual(r.status_code, 400, f"{token}: {r.text}")
        unchanged = self._list(client, case_id)
        self.assertEqual(unchanged["PolyGuard"]["properties"], {"crosslinker_ratio": 0.145, "cure_temp_c": 170.0})
        self.assertEqual(unchanged["PolyGuard"]["inputs_version"], 0)
        self.assertEqual(unchanged["EcoShield"]["id"], other)

    def test_add_candidate_refuses_unknown_fields_booleans_and_numeric_text(self):
        """Verified defects before 1D: all three were accepted and stored."""
        _, client, case_id = self._case("addstrict")
        url = f"/api/change-cases/{case_id}/candidates"
        r = client.post(url, json={"name": "X", "features": {**A_FEATURES, "colour_index": 4}})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("colour_index", r.json()["error"])
        self.assertEqual(client.post(url, json={"name": "X", "features": {"crosslinker_ratio": True, "cure_temp_c": 1}}
                                     ).status_code, 422)
        self.assertEqual(client.post(url, json={"name": "X", "features": {"crosslinker_ratio": "0.1", "cure_temp_c": 1}}
                                     ).status_code, 422)
        self.assertEqual(self._list(client, case_id), {}, "a refused candidate must store nothing")

    def test_units_are_required_and_converted_on_edit_exactly_as_on_add(self):
        org_id, client = self.org_and_client("editunits")
        spec = {"feature_columns": ["crosslinker_ratio", "cure_time_h"], "target_metric": "salt_spray_hours",
                "target_value": 300.0, "direction": "maximize",
                "units": {"cure_time_h": "h", "salt_spray_hours": "h"}}
        case_id = self.make_case(client, spec)
        add = client.post(f"/api/change-cases/{case_id}/candidates", json={
            "name": "U", "features": {"crosslinker_ratio": 0.1, "cure_time_h": 2}, "units": {"cure_time_h": "h"}})
        self.assertEqual(add.status_code, 201, add.text)
        cid = add.json()["id"]
        url = f"/api/change-cases/{case_id}/candidates/{cid}"
        values = {"crosslinker_ratio": 0.1, "cure_time_h": 90}
        no_unit = client.patch(url, json={"features": values, "units": {}})  # {} = the test says "no units"
        self.assertEqual(no_unit.status_code, 400)
        self.assertIn("unit is required", no_unit.json()["error"])
        wrong = client.patch(url, json={"features": values, "units": {"cure_time_h": "furlongs"}})
        self.assertEqual(wrong.status_code, 400)
        offset = client.patch(url, json={"features": values, "units": {"cure_time_h": "degF"}})
        self.assertEqual(offset.status_code, 400)  # no exact factor exists: refused, never guessed
        stray = client.patch(url, json={"features": values, "units": {"cure_time_h": "min", "viscosity": "cP"}})
        self.assertEqual(stray.status_code, 400)
        ok = client.patch(url, json={"features": values, "units": {"cure_time_h": "min"}})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertAlmostEqual(ok.json()["properties"]["cure_time_h"], 1.5, places=9)  # 90 min = 1.5 h
        record = ok.json()["input_record"]["cure_time_h"]
        self.assertEqual((record["value"], record["unit"]), (90, "min"))  # what was entered is kept

    def test_legacy_stored_extra_fields_are_not_shown_as_model_inputs(self):
        org_id, client, case_id = self._case("legacy")
        from backend.app.repositories import candidate_repo
        # a row written before unknown fields were refused
        candidate_repo.create_candidate(org_id, case_id, "Old", {**A_FEATURES, "colour_index": 4.0})
        view = self._list(client, case_id)["Old"]
        self.assertEqual(set(view["properties"]), {"crosslinker_ratio", "cure_temp_c"})
        self.assertEqual(view["ignored_properties"], {"colour_index": 4.0})
        # editing it stores only modelled fields
        r = self._patch(client, case_id, view["id"], {"features": A_EDITED})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["ignored_properties"])

    # ------------------------------------------------------------ stale inputs
    def test_editing_values_keeps_the_old_prediction_as_history_and_withholds_everything_derived_from_it(self):
        _, client, case_id, a, b = self._ranked_case("stale")
        before = self._list(client, case_id)
        self.assertEqual({v["rank"] for v in before.values()}, {1, 2})
        old_prediction = before["PolyGuard"]["latest_prediction"]

        r = self._patch(client, case_id, a, {"features": A_EDITED})
        self.assertEqual(r.status_code, 200, r.text)
        after = self._list(client, case_id)
        edited, other = after["PolyGuard"], after["EcoShield"]

        self.assertTrue(edited["prediction_inputs_stale"])
        self.assertEqual(edited["latest_prediction"], old_prediction, "history is kept unchanged")
        self.assertIsNone(edited["rank"])
        self.assertIsNone(edited["domain_coverage"], "coverage of the NEW values must not sit beside the OLD prediction")
        self.assertIsNone(edited["decision_support"])
        self.assertIn("previous inputs", edited["ranking_notice"])
        # the other candidate's own prediction is unchanged and still current for ITS inputs ...
        self.assertFalse(other["prediction_inputs_stale"])
        self.assertIsNotNone(other["domain_coverage"])
        # ... but the order is out of date for everyone
        self.assertTrue(other["ranking_outdated"])
        self.assertIsNone(other["rank"])
        self.assertIn("order is out of date", other["ranking_notice"])
        self.assertEqual(edited["prediction_count"], 1)

    def test_rerunning_the_ranking_makes_the_edited_candidate_current_and_keeps_the_history_rows(self):
        _, client, case_id, a, _b = self._ranked_case("rerank")
        self._patch(client, case_id, a, {"features": A_EDITED})
        results = self._rank(client, case_id)
        self.assertEqual(len(results), 2)
        after = self._list(client, case_id)
        for view in after.values():
            self.assertFalse(view["prediction_inputs_stale"])
            self.assertFalse(view["ranking_outdated"])
            self.assertIsNone(view["ranking_notice"])
        self.assertEqual({v["rank"] for v in after.values()}, {1, 2})
        self.assertEqual(after["PolyGuard"]["prediction_count"], 2, "the earlier prediction is still stored")
        self.assertEqual(after["PolyGuard"]["latest_prediction"]["predicted_value"],
                         next(r for r in results if r["candidate_id"] == a)["predicted_value"])

    def test_sufficiency_reports_that_the_rankings_no_longer_describe_the_candidates(self):
        _, client, case_id, a, _b = self._ranked_case("suff")
        url = f"/api/change-cases/{case_id}/sufficiency"
        self.assertNotIn("RANKING_MATCHES_CANDIDATES", [c["code"] for c in client.get(url).json()["checks"]])
        self._patch(client, case_id, a, {"features": A_EDITED})
        body = client.get(url).json()
        check = next(c for c in body["checks"] if c["code"] == "RANKING_MATCHES_CANDIDATES")
        self.assertEqual(check["status"], "warn")
        self.assertIn("PolyGuard", check["detail"])
        self.assertTrue(body["can_rank"], "informational only: it never blocks a re-run")

    def test_adding_a_candidate_after_a_ranking_withholds_the_order_until_it_is_rerun(self):
        _, client, case_id, _a, _b = self._ranked_case("added")
        self._add(client, case_id, "NewOne", {"crosslinker_ratio": 0.12, "cure_temp_c": 160})
        view = self._list(client, case_id)
        self.assertTrue(view["PolyGuard"]["ranking_outdated"])
        self.assertIsNone(view["PolyGuard"]["rank"])
        self.assertIsNone(view["NewOne"]["latest_prediction"])
        self._rank(client, case_id)
        view = self._list(client, case_id)
        self.assertEqual(sorted(v["rank"] for v in view.values()), [1, 2, 3])
        self.assertFalse(any(v["ranking_outdated"] for v in view.values()))

    def test_ranking_is_stamped_with_the_input_version_it_was_computed_for(self):
        org_id, client, case_id, a, _b = self._ranked_case("stamp")
        from backend.app.repositories import candidate_repo
        self.assertEqual(candidate_repo.get_latest_prediction_for_candidate(org_id, a)["inputs_version"], 0)
        self._patch(client, case_id, a, {"features": A_EDITED})
        self._rank(client, case_id)
        self.assertEqual(candidate_repo.get_latest_prediction_for_candidate(org_id, a)["inputs_version"], 1)

    def test_a_ranking_is_refused_whole_if_a_candidate_changed_while_it_ran(self):
        org_id, client, case_id, a, b = self._ranked_case("race")
        from backend.app.repositories import candidate_repo
        row = lambda cid, version: {
            "candidate_id": cid, "model_version": "gp-rank-v1", "predicted_value": 1.0, "predicted_probability": 0.5,
            "uncertainty_std": 1.0, "experiment_description": "d", "priority": 1, "inputs_version": version}
        dataset = self.datasets(org_id, case_id)[0]
        before = candidate_repo.get_candidate_usage_map(org_id, case_id)
        with self.assertRaises(candidate_repo.RankingConflict):
            candidate_repo.persist_ranking(org_id, dataset["id"], [row(a, 0), row(b, 7)])  # b is at version 0
        with self.assertRaises(candidate_repo.RankingConflict):
            candidate_repo.persist_ranking(org_id, dataset["id"], [row(a, 0), row(987654, 0)])  # no such candidate
        self.assertEqual(candidate_repo.get_candidate_usage_map(org_id, case_id), before, "nothing may be saved")

    def test_report_is_refused_after_an_edit_and_never_uses_the_prediction_for_old_values(self):
        """It used to generate a report that left the edited candidate out ("Not yet ranked: PolyGuard") but still
        printed a ranked table for the rest, i.e. a partial ranking from before the edit. A report now needs a
        ranking that still describes the candidates as they are (see test_report_ranking_currency.py)."""
        _, client, case_id, a, _b = self._ranked_case("report")
        self._patch(client, case_id, a, {"features": A_EDITED})
        resp = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("PolyGuard", resp.json()["error"])
        self.assertIn("Input values were changed", resp.json()["error"])
        self._rank(client, case_id)
        resp = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(resp.status_code, 200, resp.text)
        text = self.docx_text(resp.content)
        self.assertNotIn("Not yet ranked: PolyGuard", text)
        self.assertIn("EcoShield", text)

    # ------------------------------------------------------------ delete / remove from analysis
    def test_an_unranked_candidate_can_be_permanently_deleted(self):
        org_id, client, case_id = self._case("delunranked")
        cid = self._add(client, case_id, "Mistake", A_FEATURES)
        self.assertTrue(self._list(client, case_id)["Mistake"]["deletable"])
        r = client.delete(f"/api/change-cases/{case_id}/candidates/{cid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"deleted": True, "id": cid, "name": "Mistake"})
        self.assertEqual(self._list(client, case_id, archived=True), {})
        from backend.app.repositories import candidate_repo
        self.assertIsNone(candidate_repo.get_candidate(org_id, cid))
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}/candidates/{cid}").status_code, 404)
        # the name is free again
        self._add(client, case_id, "Mistake", A_FEATURES)

    def test_a_ranked_candidate_cannot_be_deleted_but_can_be_removed_from_the_analysis_and_restored(self):
        org_id, client, case_id, a, b = self._ranked_case("remove")
        refused = client.delete(f"/api/change-cases/{case_id}/candidates/{a}")
        self.assertEqual(refused.status_code, 400)
        self.assertIn("remove it from the analysis instead", refused.json()["error"])
        self.assertIn("PolyGuard", self._list(client, case_id))

        removed = client.post(f"/api/change-cases/{case_id}/candidates/{a}/archive")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["archived"])
        self.assertNotIn("PolyGuard", self._list(client, case_id), "hidden from the default list")
        kept = self._list(client, case_id, archived=True)["PolyGuard"]
        self.assertTrue(kept["archived"])
        self.assertEqual(kept["prediction_count"], 1, "its prediction is retained")
        self.assertIsNone(kept["rank"])
        # the remaining candidate's order was produced by a run that included the removed one
        remaining = self._list(client, case_id)["EcoShield"]
        self.assertTrue(remaining["ranking_outdated"])
        self.assertIsNone(remaining["rank"])
        # archived candidates take no part in the next ranking
        results = self._rank(client, case_id)
        self.assertEqual([r["candidate_name"] for r in results], ["EcoShield"])
        self.assertEqual(self._list(client, case_id)["EcoShield"]["rank"], 1)
        self.assertEqual(self._list(client, case_id, archived=True)["PolyGuard"]["prediction_count"], 1)

        again = client.post(f"/api/change-cases/{case_id}/candidates/{a}/archive")
        self.assertEqual(again.status_code, 400)  # already removed

        restored = client.post(f"/api/change-cases/{case_id}/candidates/{a}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)
        view = self._list(client, case_id)
        self.assertIn("PolyGuard", view)
        self.assertTrue(view["EcoShield"]["ranking_outdated"], "restoring changes the set again")
        self.assertIsNone(view["EcoShield"]["rank"])
        self._rank(client, case_id)
        self.assertEqual(sorted(v["rank"] for v in self._list(client, case_id).values()), [1, 2])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/candidates/{a}/restore").status_code, 400)

    def test_a_removed_candidate_cannot_be_edited_until_restored_and_its_name_stays_reserved(self):
        _, client, case_id, a, _b = self._ranked_case("removededit")
        client.post(f"/api/change-cases/{case_id}/candidates/{a}/archive")
        self.assertEqual(self._patch(client, case_id, a, {"name": "Other"}).status_code, 400)
        dup = client.post(f"/api/change-cases/{case_id}/candidates",
                          json={"name": "polyguard", "features": A_FEATURES})
        self.assertEqual(dup.status_code, 400, "restoring must never create a duplicate name")

    # ------------------------------------------------------------ recorded outcomes
    def _record_outcome(self, client, case_id, cid, passed=True):
        return client.post(f"/api/change-cases/{case_id}/candidates/{cid}/outcome",
                           json={"actual_result": "Passed salt spray at 540 h", "passed_spec": passed})

    def test_a_recorded_outcome_is_never_deleted_and_locks_the_input_values(self):
        org_id, client, case_id, a, _b = self._ranked_case("outcome")
        self.assertEqual(self._record_outcome(client, case_id, a).status_code, 201)
        view = self._list(client, case_id)["PolyGuard"]
        self.assertFalse(view["deletable"])
        self.assertFalse(view["features_editable"])
        self.assertEqual(view["outcome_count"], 1)

        delete = client.delete(f"/api/change-cases/{case_id}/candidates/{a}")
        self.assertEqual(delete.status_code, 400)
        self.assertIn("physical validation outcome", delete.json()["error"])
        locked = self._patch(client, case_id, a, {"features": A_EDITED})
        self.assertEqual(locked.status_code, 400)
        self.assertIn("outcome has been recorded", locked.json()["error"])
        self.assertEqual(self._patch(client, case_id, a, {"name": "PolyGuard (tested)"}).status_code, 200)

        # removing it from the analysis keeps the outcome and the report still names the candidate
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/candidates/{a}/archive").status_code, 200)
        from backend.app.repositories import candidate_repo
        self.assertEqual(len(candidate_repo.list_outcomes_for_change_case(org_id, case_id)), 1)
        self._add(client, case_id, "Another", {"crosslinker_ratio": 0.12, "cure_temp_c": 160})
        self._rank(client, case_id)
        report = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertIn("PolyGuard (tested)", self.docx_text(report.content))
        # an archived candidate takes no new outcome
        self.assertEqual(self._record_outcome(client, case_id, a).status_code, 400)

    # ------------------------------------------------------------ closed / archived diagnostics
    def test_a_completed_diagnostic_refuses_every_candidate_change(self):
        _, client, case_id, a, _b = self._ranked_case("completed")
        spare = self._add(client, case_id, "Spare", {"crosslinker_ratio": 0.12, "cure_temp_c": 160})
        for status in ("active", "completed"):
            self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": status}).status_code, 200)
        base = f"/api/change-cases/{case_id}/candidates"
        self.assertEqual(self._patch(client, case_id, a, {"name": "x"}).status_code, 400)
        self.assertEqual(client.delete(f"{base}/{spare}").status_code, 400)
        self.assertEqual(client.post(f"{base}/{a}/archive").status_code, 400)
        self.assertEqual(client.post(f"{base}/{a}/restore").status_code, 400)
        self.assertIn("Spare", self._list(client, case_id), "nothing was deleted")

    def test_an_archived_diagnostic_is_read_only_for_candidates_too(self):
        _, client, case_id, a, _b = self._ranked_case("archivedcase")
        spare = self._add(client, case_id, "Spare", {"crosslinker_ratio": 0.12, "cure_temp_c": 160})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/archive").status_code, 200)
        base = f"/api/change-cases/{case_id}/candidates"
        self.assertEqual(self._patch(client, case_id, a, {"name": "x"}).status_code, 400)
        self.assertEqual(client.delete(f"{base}/{spare}").status_code, 400)
        self.assertEqual(client.post(f"{base}/{a}/archive").status_code, 400)
        self.assertEqual(client.get(base).status_code, 200, "reading stays possible")

    # ------------------------------------------------------------ isolation
    def test_other_organizations_and_other_diagnostics_get_404_and_nothing_changes(self):
        _, client_a, case_a, a, _b = self._ranked_case("isoa")
        _, client_b, case_b = self._case("isob")
        other_org_candidate = self._add(client_b, case_b, "Theirs", A_FEATURES)
        base = f"/api/change-cases/{case_a}/candidates/{a}"
        # another organization, by the real id
        self.assertEqual(client_b.patch(base, json={"name": "hijack"}).status_code, 404)
        self.assertEqual(client_b.post(f"{base}/archive").status_code, 404)
        self.assertEqual(client_b.post(f"{base}/restore").status_code, 404)
        self.assertEqual(client_b.delete(base).status_code, 404)
        self.assertEqual(client_b.get(f"/api/change-cases/{case_a}/candidates").status_code, 404)
        # my own diagnostic, but another organization's candidate id (and my own other diagnostic's)
        mine = f"/api/change-cases/{case_b}/candidates"
        self.assertEqual(client_b.patch(f"{mine}/{a}", json={"name": "hijack"}).status_code, 404)
        self.assertEqual(client_b.delete(f"{mine}/{a}").status_code, 404)
        second_case = self.make_case(client_b, {
            "feature_columns": ["crosslinker_ratio", "cure_temp_c"], "target_metric": "salt_spray_hours",
            "target_value": 300.0, "direction": "maximize"})
        wrong_case = f"/api/change-cases/{second_case}/candidates/{other_org_candidate}"
        self.assertEqual(client_b.patch(wrong_case, json={"name": "moved"}).status_code, 404)
        self.assertEqual(client_b.delete(wrong_case).status_code, 404)
        survivors = self._list(client_a, case_a)
        self.assertEqual(set(survivors), {"PolyGuard", "EcoShield"})
        self.assertEqual(survivors["PolyGuard"]["inputs_version"], 0)
        self.assertIn("Theirs", self._list(client_b, case_b))

    # ------------------------------------------------------------ audit trail
    def test_edits_are_audited_with_the_previous_values(self):
        org_id, client, case_id, a, _b = self._ranked_case("audit")
        self._patch(client, case_id, a, {"features": A_EDITED})
        from backend.app.config.database import db_connection
        from sqlalchemy import text
        with db_connection() as conn:
            rows = [r[0] for r in conn.execute(
                text("SELECT detail FROM audit_logs WHERE organization_id = :o AND action = 'candidate.update'"),
                {"o": org_id}).all()]
        self.assertEqual(len(rows), 1)
        self.assertIn(f"candidate_id={a}", rows[0])
        self.assertIn("fields=features", rows[0])
        self.assertIn('"cure_temp_c": 170', rows[0])  # the values that were replaced are not lost


class TestCandidateMigration(unittest.TestCase):
    """The 1D migration, on its own database: additive, defaults keep every existing row current, and
    it downgrades and upgrades again without losing data."""

    def test_upgrade_downgrade_upgrade_keeps_existing_rows(self):
        from alembic import command
        from alembic.config import Config
        from sqlalchemy import create_engine, text

        from backend.app.config import database
        from backend.app.config.settings import settings

        directory = os.path.join(os.path.dirname(__file__), "_test_env_cand_migration")
        shutil.rmtree(directory, ignore_errors=True)
        os.makedirs(directory)
        url = f"sqlite:///{directory}/m.db"
        saved_url, saved_engine = settings.DATABASE_URL, database.engine
        try:
            settings.DATABASE_URL = url
            database.engine = database._make_engine()
            backend_dir = os.path.join(os.path.dirname(__file__), "..")
            cfg = Config(os.path.join(backend_dir, "alembic.ini"))
            cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
            command.upgrade(cfg, "d4a7f1c20e95")  # the schema as it was before 1D
            engine = create_engine(url)
            with engine.begin() as conn:
                conn.execute(text("PRAGMA foreign_keys=ON"))
                conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('Old Co', 't')"))
                org = conn.execute(text("SELECT id FROM organizations")).scalar()
                conn.execute(text(
                    "INSERT INTO change_cases (organization_id, name, trigger_type, qualification_spec_json, status, "
                    "created_at, updated_at) VALUES (:o, 'c', 'other', '{}', 'draft', 't', 't')"), {"o": org})
                case = conn.execute(text("SELECT id FROM change_cases")).scalar()
                conn.execute(text(
                    "INSERT INTO qualification_datasets (organization_id, change_case_id, original_filename, "
                    "stored_filename, row_count, created_at) VALUES (:o, :c, 'f.csv', 's', 1, 't')"), {"o": org, "c": case})
                ds = conn.execute(text("SELECT id FROM qualification_datasets")).scalar()
                conn.execute(text(
                    "INSERT INTO candidate_substitutes (organization_id, change_case_id, candidate_name, "
                    "properties_json, created_at) VALUES (:o, :c, 'Old', '{}', 't')"), {"o": org, "c": case})
                cand = conn.execute(text("SELECT id FROM candidate_substitutes")).scalar()
                conn.execute(text(
                    "INSERT INTO predictions (organization_id, candidate_id, dataset_version_id, model_version, "
                    "predicted_probability, uncertainty_std, created_at) VALUES (:o, :k, :d, 'v', 0.5, 1.0, 't')"),
                    {"o": org, "k": cand, "d": ds})

            command.upgrade(cfg, "head")
            with engine.connect() as conn:
                cols = lambda table: {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).all()}
                self.assertTrue({"inputs_version", "archived_at", "restored_at"} <= cols("candidate_substitutes"))
                self.assertIn("inputs_version", cols("predictions"))
                self.assertEqual(conn.execute(text("SELECT inputs_version, archived_at, restored_at "
                                                   "FROM candidate_substitutes")).one(), (0, None, None))
                self.assertEqual(conn.execute(text("SELECT inputs_version FROM predictions")).scalar(), 0)

            command.downgrade(cfg, "d4a7f1c20e95")
            with engine.connect() as conn:
                self.assertNotIn("inputs_version", {r[1] for r in conn.execute(text("PRAGMA table_info(predictions)")).all()})
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM candidate_substitutes")).scalar(), 1)
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM predictions")).scalar(), 1)

            command.upgrade(cfg, "head")  # re-upgrade after a downgrade must work
            with engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT inputs_version FROM candidate_substitutes")).scalar(), 0)
            engine.dispose()
        finally:
            settings.DATABASE_URL = saved_url
            database.engine.dispose()
            database.engine = saved_engine
            shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()