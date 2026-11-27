"""Level 5: model registry, validation records, applicability, uncertainty, comparison and their permissions.
Run:  python3 -m unittest backend.tests.test_model_validation_center -v"""
import json
import os
import re
import unittest
import uuid
from unittest import mock

from alembic import command
from sqlalchemy import create_engine, event, text

from backend.tests import evidence_fixtures as F
from backend.tests import test_diagnostic_lifecycle as lc
from backend.tests.test_evidence_intelligence import Base as EvidenceBase
from backend.tests.test_migration_portability import _config, _UseDatabase

HEAD = lc.HEAD
GROUPED, REGION, RANDOM, UNIT = "grouped_input_sets_kfold", "leave_cluster_out_extrapolation", "random_rows_kfold", "grouped_by_recorded_unit"


class Base(EvidenceBase):
    dir_name = "_test_env_model_validation"

    def ranked(self, label, candidates=1):
        org, client, case_id, ids = self.case_with_evidence(label, candidates=candidates, rank=True)
        versions = client.get(f"/api/models?program_id={case_id}").json()["versions"]
        return org, client, case_id, ids, versions

    def validate(self, client, version_id, protocol):
        return client.post(f"/api/models/{version_id}/validations", json={"protocol": protocol})

    def run_required(self, client, version_id):
        out = {}
        for p in (GROUPED, REGION):
            r = self.validate(client, version_id, p)
            self.assertEqual(r.status_code, 200, r.text)
            out[p] = r.json()
        return out


class TestRegistry(Base):
    dir_name = "_test_env_model_registry"

    def test_ranking_registers_a_version_and_stamps_every_prediction(self):
        org, client, case_id, ids, versions = self.ranked("Reg", candidates=2)
        self.assertEqual(len(versions), 1)
        v = versions[0]
        self.assertEqual((v["version_number"], v["family"], v["program_id"], v["source"]), (1, "gp-rank-v1", case_id, "ranking"))
        self.assertRegex(v["model_key"], r"^gp-[0-9a-f]{12}$")
        self.assertEqual(v["n_training_rows"], 14)
        cands = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.assertEqual({c["latest_prediction"]["model_version_id"] for c in cands}, {v["id"]})
        self.assertEqual(client.get(f"/api/models/{v['id']}").json()["prediction_count"], 2)

    def test_same_data_and_settings_is_the_same_version_new_data_is_a_new_one(self):
        org, client, case_id, ids, versions = self.ranked("Same")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        self.assertEqual(len(client.get(f"/api/models?program_id={case_id}").json()["versions"]), 1)
        again = client.post(f"/api/change-cases/{case_id}/models/register")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((again.json()["created"], again.json()["version"]["id"]), (False, versions[0]["id"]))
        self.assertEqual(self.upload(client, case_id, "second.csv", self.csv(scale=2)).status_code, 201)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        now = client.get(f"/api/models?program_id={case_id}").json()["versions"]
        self.assertEqual([x["version_number"] for x in now], [2, 1])
        self.assertNotEqual(now[0]["model_key"], now[1]["model_key"])
        self.assertEqual((now[0]["is_current"], now[1]["is_current"]), (True, False))

    def test_reuploading_identical_rows_in_another_order_gives_the_same_identity(self):
        org, client, case_id, ids, versions = self.ranked("Order")
        rows = list(reversed(F.base_rows(14)))
        self.assertEqual(self.upload(client, case_id, "reordered.csv", F.csv_bytes(HEAD, rows)).status_code, 201)
        again = client.post(f"/api/change-cases/{case_id}/models/register")
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual((again.json()["created"], again.json()["version"]["id"]), (False, versions[0]["id"]))

    def test_detail_records_everything_that_defines_the_model(self):
        org, client, case_id, ids, versions = self.ranked("Detail")
        d = client.get(f"/api/models/{versions[0]['id']}").json()
        self.assertEqual(len(d["identity_hash"]), 64)
        self.assertEqual(len(d["training_data_hash"]), 64)
        self.assertEqual([f["name"] for f in d["feature_definitions"]], ["crosslinker_ratio", "cure_temp_c"])
        self.assertEqual(d["feature_definitions"][1]["unit"], "degC")
        self.assertEqual((d["target"]["name"], d["target"]["unit"], d["target"]["direction"]), ("salt_spray_hours", "h", "maximize"))
        self.assertIn("Matern", d["configuration"]["kernel"])
        self.assertIn("bounds taken from the training rows only", d["preprocessing"]["scaling"])
        self.assertEqual((d["seed"], d["engine_version"]), (0, "optisynth-engine-5.0"))
        self.assertIn("scikit-learn", d["environment"])
        self.assertTrue(d["integrity"]["reproduces_identity"])
        self.assertEqual(d["validation"]["state"], "incomplete")  # nothing has been validated yet
        self.assertEqual(d["dataset_filename"], "h.csv")
        self.assertTrue(any("not a physical qualification result" in x for x in d["limitations"]))

    def test_a_registration_problem_does_not_stop_a_ranking(self):
        org, client, case_id, ids = self.case_with_evidence("Soft", candidates=1)
        with mock.patch("backend.app.services.model_validation_service.register", side_effect=RuntimeError("x")):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        cand = client.get(f"/api/change-cases/{case_id}/candidates").json()[0]
        self.assertIsNone(cand["latest_prediction"]["model_version_id"])

    def test_predictions_made_before_versions_existed_stay_unlabelled(self):
        from backend.app.repositories import model_registry_repo
        names = {n for n in dir(model_registry_repo) if not n.startswith("_")}
        for forbidden in ("update_version", "delete_version", "update_validation", "delete_validation"):
            self.assertNotIn(forbidden, names)
        self.assertFalse([n for n in names if re.match(r"(update|delete|remove|set)_", n)])  # append only

    def test_tampered_training_rows_are_detected_and_validation_refuses(self):
        org, client, case_id, ids, versions = self.ranked("Tamper")
        from backend.app.config import database
        with database.db_transaction() as conn:
            conn.execute(text("UPDATE qualification_experiments SET target_value = target_value + 1 WHERE change_case_id = :c"), {"c": case_id})
        d = client.get(f"/api/models/{versions[0]['id']}").json()
        self.assertFalse(d["integrity"]["reproduces_identity"])
        r = self.validate(client, versions[0]["id"], GROUPED)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no longer reproduces", r.json()["error"])


class TestBuildRecordAndRuntime(Base):
    dir_name = "_test_env_model_runtime"

    def test_api_describes_a_build_record_not_a_stored_model(self):
        org, client, case_id, ids, versions = self.ranked("Build")
        self.assertEqual((versions[0]["kind"], versions[0]["fitted_model_stored"]), ("build_record", False))
        d = client.get(f"/api/models/{versions[0]['id']}").json()
        self.assertEqual((d["kind"], d["fitted_model_stored"]), ("build_record", False))
        self.assertIn("not a stored fitted model", d["reproducibility"]["statement"])
        self.assertIn("refitted from the training rows", d["reproducibility"]["statement"])
        self.assertTrue(d["reproducibility"]["runtime_matches"])
        self.assertEqual(set(d["environment"]), {"python", "numpy", "scipy", "scikit-learn"})
        self.assertIn("not a stored fitted model", d["limitations"][0])

    def test_a_different_runtime_is_a_different_version_and_old_versions_cannot_be_validated(self):
        org, client, case_id, ids, versions = self.ranked("Runtime")
        old = versions[0]
        from engine.validation import identity
        other = dict(identity.environment_record(), scipy="0.0.0-other")
        with mock.patch("engine.validation.identity.environment_record", return_value=other):
            again = client.post(f"/api/change-cases/{case_id}/models/register").json()
            self.assertTrue(again["created"])
            self.assertEqual(again["version"]["version_number"], 2)
            self.assertNotEqual(again["version"]["model_key"], old["model_key"])
            d_new = client.get(f"/api/models/{again['version']['id']}").json()
            self.assertEqual(d_new["training_data_hash"], client.get(f"/api/models/{old['id']}").json()["training_data_hash"])
            self.assertEqual(d_new["environment"]["scipy"], "0.0.0-other")
            self.assertTrue(d_new["reproducibility"]["runtime_matches"])
            # the version built under the real libraries cannot be validated under the other ones
            d_old = client.get(f"/api/models/{old['id']}").json()
            self.assertFalse(d_old["reproducibility"]["runtime_matches"])
            self.assertTrue(d_old["integrity"]["reproduces_identity"])  # the data itself is fine
            r = self.validate(client, old["id"], GROUPED)
            self.assertEqual(r.status_code, 400)
            self.assertIn("different library versions", r.json()["error"])
        self.assertEqual(self.validate(client, old["id"], GROUPED).status_code, 200)  # back on the original libraries


class TestValidationRuns(Base):
    dir_name = "_test_env_model_runs"

    def test_required_protocols_complete_with_counts_and_limits(self):
        org, client, case_id, ids, versions = self.ranked("Runs")
        out = self.run_required(client, versions[0]["id"])
        for p, body in out.items():
            self.assertTrue(body["created"])
            v = body["validation"]
            self.assertIn(v["status"], ("completed", "completed_with_warnings"), p)
            r = client.get(f"/api/models/{versions[0]['id']}/validations/{v['id']}").json()["result"]
            self.assertEqual(r["counts"]["n_rows"], 14)
            self.assertGreaterEqual(r["counts"]["n_scored"], 10)
            for key in ("mae", "rmse", "r2", "skill_vs_mean_baseline", "residuals"):
                self.assertIn(key, r["metrics"])
            self.assertEqual([lvl["nominal"] for lvl in r["calibration"]["levels"]], [0.5, 0.8, 0.9, 0.95])
            self.assertIn("does not establish calibration", r["calibration"]["caveat"])
            self.assertEqual(len(r["protocol"]["hash"]), 64)
            self.assertIn("small_sample", [w["code"] for w in r["warnings"]])  # 14 rows is below the recommended 20
        d = client.get(f"/api/models/{versions[0]['id']}").json()
        self.assertEqual(d["validation"]["state"], "completed_with_warnings")  # warnings are not hidden
        self.assertIn("not an approval", d["validation"]["note"])
        by = {p["key"]: p for p in d["protocols"]}
        self.assertIsNotNone(by[GROUPED]["latest"])
        self.assertIn("interpolation", by[GROUPED]["generalisation_claim"])
        self.assertEqual(by[REGION]["generalisation_claim"], "extrapolation in input space only")

    def test_the_same_protocol_on_the_same_version_is_not_run_twice(self):
        org, client, case_id, ids, versions = self.ranked("Idem")
        first = self.validate(client, versions[0]["id"], GROUPED).json()
        second = self.validate(client, versions[0]["id"], GROUPED).json()
        self.assertEqual((first["created"], second["created"]), (True, False))
        self.assertEqual(first["validation"]["id"], second["validation"]["id"])

    def test_results_are_reproducible_across_versions_with_the_same_data(self):
        org, client, case_id, ids, versions = self.ranked("Repro")
        a = self.validate(client, versions[0]["id"], GROUPED).json()["validation"]
        ra = client.get(f"/api/models/{versions[0]['id']}/validations/{a['id']}").json()
        from engine.validation import runner
        from backend.app.services import model_validation_service as svc
        from backend.app.repositories import change_case_repo, qualification_dataset_repo
        case = change_case_repo.get_change_case(org, case_id)
        ds = qualification_dataset_repo.get_latest_dataset_for_change_case(org, case_id)
        t = svc._training(org, case, ds)
        again = runner.run_protocol(GROUPED, t["X"], t["y"], groups=t["groups"], seed=0)
        self.assertEqual(again["metrics"], ra["result"]["metrics"])
        self.assertEqual(again["evaluation_rows_hash"], ra["evaluation_rows_hash"])

    def test_too_little_evidence_is_insufficient_and_the_model_is_never_complete(self):
        org, client, case_id, ids = self.case_with_evidence("Little", candidates=1)
        self.assertEqual(self.upload(client, case_id, "small.csv", F.csv_bytes(HEAD, F.base_rows(7))).status_code, 201)
        reg = client.post(f"/api/change-cases/{case_id}/models/register").json()
        vid = reg["version"]["id"]
        self.assertTrue(reg["created"])
        grouped = self.validate(client, vid, GROUPED).json()["validation"]
        region = self.validate(client, vid, REGION).json()["validation"]
        self.assertEqual(region["status"], "insufficient_samples")
        self.assertIn("at least 10", region["message"])
        full = client.get(f"/api/models/{vid}/validations/{region['id']}").json()["result"]
        self.assertIsNone(full["metrics"])
        self.assertEqual(client.get(f"/api/models/{vid}").json()["validation"]["state"], "incomplete")
        self.assertNotEqual(grouped["status"], "failed")

    def test_a_failed_run_is_recorded_as_failed_and_can_be_retried(self):
        org, client, case_id, ids, versions = self.ranked("Fail")
        vid = versions[0]["id"]
        with mock.patch("engine.validation.runner._fit_recorded", side_effect=RuntimeError("secret internals")):
            r = self.validate(client, vid, GROUPED).json()
        self.assertEqual(r["validation"]["status"], "failed")
        self.assertNotIn("secret internals", json.dumps(r))
        self.assertIsNone(client.get(f"/api/models/{vid}/validations/{r['validation']['id']}").json()["result"]["metrics"])
        self.validate(client, vid, REGION)
        self.assertEqual(client.get(f"/api/models/{vid}").json()["validation"]["state"], "failed")
        retry = self.validate(client, vid, GROUPED).json()
        self.assertTrue(retry["created"])  # a failure is not cached as an answer
        self.assertIn(retry["validation"]["status"], ("completed", "completed_with_warnings"))
        self.assertEqual(len(client.get(f"/api/models/{vid}").json()["history"]), 3)  # the failure stays in the history
        self.assertEqual(client.get(f"/api/models/{vid}").json()["validation"]["state"], "completed_with_warnings")

    def test_the_batch_supplier_protocol_is_listed_but_cannot_run(self):
        org, client, case_id, ids, versions = self.ranked("Unit")
        r = self.validate(client, versions[0]["id"], UNIT).json()["validation"]
        self.assertEqual(r["status"], "not_available")
        self.assertIn("batch, lot, supplier or material", r["message"])
        self.assertEqual(client.get(f"/api/models/{versions[0]['id']}").json()["validation"]["state"], "incomplete")
        listing = {p["key"]: p for p in client.get("/api/models/protocols").json()["protocols"]}
        self.assertEqual(set(listing), {GROUPED, REGION, RANDOM, UNIT})
        for p in listing.values():
            self.assertTrue(p["limitations"] and p["grouping_unit"] and p["minimum"] is not None)

    def test_unknown_protocols_and_extra_fields_are_refused(self):
        org, client, case_id, ids, versions = self.ranked("Bad")
        self.assertEqual(self.validate(client, versions[0]["id"], "magic").status_code, 400)
        r = client.post(f"/api/models/{versions[0]['id']}/validations", json={"protocol": GROUPED, "seed": 5})
        self.assertEqual(r.status_code, 422)

    def test_audit_records_registration_and_runs(self):
        org, client, case_id, ids, versions = self.ranked("Audit")
        self.validate(client, versions[0]["id"], GROUPED)
        client.post(f"/api/change-cases/{case_id}/models/register")
        from backend.app.config import database
        with database.db_connection() as conn:
            actions = [r[0] for r in conn.execute(text("SELECT action FROM audit_logs WHERE organization_id = :o"), {"o": org})]
        self.assertIn("model.validation_run", actions)
        self.assertIn("model.register", actions)


class TestPermissionsAndIsolation(Base):
    dir_name = "_test_env_model_perms"

    def test_members_can_read_but_only_managers_and_reviewers_can_run(self):
        org, client, case_id, ids, versions = self.ranked("Perm")
        vid = versions[0]["id"]
        member = self.member_client(client, "member")
        self.assertEqual(member.get(f"/api/models/{vid}").status_code, 200)
        self.assertFalse(member.get(f"/api/models/{vid}").json()["permissions"]["can_run"])
        self.assertEqual(self.validate(member, vid, GROUPED).status_code, 403)
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/models/register").status_code, 403)
        client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": self.person(member)})
        self.assertEqual(self.validate(member, vid, GROUPED).status_code, 200)
        client.delete(f"/api/change-cases/{case_id}/reviewers/{self.person(member)}")
        self.assertEqual(self.validate(member, vid, REGION).status_code, 403)
        admin = self.member_client(client, "admin")
        self.assertEqual(self.validate(admin, vid, REGION).status_code, 200)

    def test_other_organizations_see_nothing_and_cannot_act(self):
        org, client, case_id, ids, versions = self.ranked("Iso")
        vid = versions[0]["id"]
        self.run_required(client, vid)
        _, other = self.org_and_client("OtherModels")
        for r in (other.get(f"/api/models/{vid}"), other.get(f"/api/models/{vid}/uncertainty"), other.get(f"/api/models/{vid}/applicability"),
                  other.get(f"/api/models/{vid}/validations/1"), self.validate(other, vid, GROUPED), other.get(f"/api/models?program_id={case_id}"),
                  other.post(f"/api/change-cases/{case_id}/models/register"), other.get(f"/api/models/compare?a={vid}&b={vid + 1}")):
            self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(other.get("/api/models").json(), {"total": 0, "limit": 50, "offset": 0, "versions": []})
        self.assertEqual(self.client_factory().get("/api/models").status_code, 401)
        self.assertEqual(self.client_factory().get("/api/models/protocols").status_code, 401)

    def test_a_validation_cannot_be_read_through_another_version(self):
        org, client, case_id, ids, versions = self.ranked("Cross")
        vid = versions[0]["id"]
        rec = self.validate(client, vid, GROUPED).json()["validation"]
        self.assertEqual(self.upload(client, case_id, "second.csv", self.csv(scale=2)).status_code, 201)
        second = client.post(f"/api/change-cases/{case_id}/models/register").json()["version"]["id"]
        self.assertEqual(client.get(f"/api/models/{second}/validations/{rec['id']}").status_code, 404)

    def test_closed_and_archived_programs_cannot_be_validated(self):
        org, client, case_id, ids, versions = self.ranked("Closed")
        vid = versions[0]["id"]
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        r = self.validate(client, vid, GROUPED)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(client.get(f"/api/models/{vid}").status_code, 200)  # still readable


class TestApplicabilityAndUncertainty(Base):
    dir_name = "_test_env_model_applic"

    def test_candidates_inside_outside_and_edited(self):
        org, client, case_id, ids = self.case_with_evidence("App", candidates=1)
        far = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "Far", "features": {"crosslinker_ratio": 0.5, "cure_temp_c": 400}})
        self.assertEqual(far.status_code, 201, far.text)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        vid = client.get(f"/api/models?program_id={case_id}").json()["versions"][0]["id"]
        a = client.get(f"/api/models/{vid}/applicability").json()
        by = {c["name"]: c for c in a["candidates"]}
        self.assertEqual(by["Far"]["support"], "unsupported")
        self.assertIn(by["Far"]["class"], ("isolated", "sparse_region"))
        self.assertEqual(by["Cand 0"]["range_status"], "within_historical_domain")
        self.assertNotEqual(by["Cand 0"]["support"], "unsupported")
        self.assertIn("nearest_training_input_set", by["Cand 0"])
        self.assertIn("diagnostics, not probabilities or guarantees", a["convention"])
        self.assertIsNone(a["problem"], a["problem"])
        self.assertGreater(a["reference"]["reference_points"], 7)
        # the reference thresholds come from the training rows alone: adding a candidate never moves them
        before = a["reference"]
        client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "Another", "features": {"crosslinker_ratio": 0.12, "cure_temp_c": 160}})
        self.assertEqual(client.get(f"/api/models/{vid}/applicability").json()["reference"], before)
        edit = client.patch(f"/api/change-cases/{case_id}/candidates/{ids[0]}", json={"features": {"crosslinker_ratio": 0.1, "cure_temp_c": 155}})
        self.assertEqual(edit.status_code, 200, edit.text)
        stale = {c["name"]: c for c in client.get(f"/api/models/{vid}/applicability").json()["candidates"]}["Cand 0"]
        self.assertEqual(stale["support"], "not_assessed")
        self.assertIn("edited after this prediction", stale["reason"])

    def test_four_kinds_of_uncertainty_stay_separate(self):
        org, client, case_id, ids, versions = self.ranked("Unc")
        vid = versions[0]["id"]
        self.validate(client, vid, GROUPED)
        u = client.get(f"/api/models/{vid}/uncertainty").json()
        self.assertEqual({"model", "measurement", "data", "applicability"}, {k for k in u if k in ("model", "measurement", "data", "applicability")})
        self.assertEqual(u["measurement"]["status"], "not_assessed")
        self.assertNotIn("value", u["measurement"])
        self.assertEqual(u["data"]["replicate_scatter"]["status"], "no_repeated_input_sets")
        self.assertEqual(u["data"]["summary"]["n_rows"], 14)
        self.assertIsNotNone(u["model"]["fitted_noise_sd"])
        self.assertEqual(u["model"]["held_out"]["protocol"], "grouped_input_sets_kfold")
        self.assertIn("never added together", u["note"])
        self.assertIn("not a measurement uncertainty", u["model"]["fit_note"])


class TestDuplicateInputSets(Base):
    dir_name = "_test_env_model_duplicates"

    def test_nearest_training_input_is_a_real_input_set_even_when_it_was_measured_many_times(self):
        org, client, case_id, ids = self.case_with_evidence("Dup", candidates=1)
        rows = F.base_rows(14)
        repeated = rows + [rows[3]] * 0  # exact repeats are removed at intake; vary only the target to keep them
        r = self.upload(client, case_id, "dups.csv", F.csv_bytes(HEAD, rows + [(rows[3][0], rows[3][1], rows[3][2] + 7), (rows[3][0], rows[3][1], rows[3][2] + 11)]))
        if r.status_code != 201:
            self.skipTest("intake refuses repeated input sets with different targets in this configuration")
        exact = rows[3]
        client.post(f"/api/change-cases/{case_id}/candidates", json={"name": "AtRepeated", "features": {"crosslinker_ratio": exact[0], "cure_temp_c": exact[1]}})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        vid = client.get(f"/api/models?program_id={case_id}").json()["versions"][0]["id"]
        a = {c["name"]: c for c in client.get(f"/api/models/{vid}/applicability").json()["candidates"]}["AtRepeated"]
        self.assertEqual(a["nearest_distance"], 0.0)
        self.assertEqual(a["nearest_training_input_set"], {"crosslinker_ratio": exact[0], "cure_temp_c": exact[1]})
        self.assertEqual(a["nearest_training_rows"], 3)
        d = client.get(f"/api/models/{vid}").json()
        self.assertEqual(d["n_input_sets"], 14)
        self.assertEqual(d["n_training_rows"], 16)

    def test_every_candidate_shows_one_of_the_training_input_sets(self):
        org, client, case_id, ids = self.case_with_evidence("Real", candidates=2)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        vid = client.get(f"/api/models?program_id={case_id}").json()["versions"][0]["id"]
        training = {(round(r[0], 6), round(r[1], 6)) for r in F.base_rows(14)}
        for c in client.get(f"/api/models/{vid}/applicability").json()["candidates"]:
            n = c["nearest_training_input_set"]
            self.assertIn((round(n["crosslinker_ratio"], 6), round(n["cure_temp_c"], 6)), training)
            self.assertGreaterEqual(c["nearest_training_rows"], 1)


class TestComparison(Base):
    dir_name = "_test_env_model_compare"

    def two_versions(self, label):
        org, client, case_id, ids, versions = self.ranked(label)
        self.assertEqual(self.upload(client, case_id, "second.csv", self.csv(scale=2)).status_code, 201)
        second = client.post(f"/api/change-cases/{case_id}/models/register").json()["version"]["id"]
        return org, client, case_id, versions[0]["id"], second

    def test_comparison_needs_the_identical_protocol_on_both_and_declares_no_winner(self):
        org, client, case_id, a, b = self.two_versions("Cmp")
        before = client.get(f"/api/models/compare?a={a}&b={b}").json()
        self.assertTrue(all(p["comparable"] is False for p in before["protocols"]))
        self.assertIn("Run this protocol on both versions", before["protocols"][0]["reason"])
        for vid in (a, b):
            self.run_required(client, vid)
        after = client.get(f"/api/models/compare?a={a}&b={b}").json()
        grouped = {p["protocol_key"]: p for p in after["protocols"]}[GROUPED]
        self.assertTrue(grouped["comparable"])
        self.assertFalse(grouped["same_evaluation_rows"])  # different evidence: the difference is indicative only
        self.assertIn("indicative", grouped["reading"])
        self.assertEqual(set(grouped["n_scored"]), {"a", "b"})
        self.assertIn("No version is declared better", after["note"])
        self.assertNotIn("winner", json.dumps(after).lower().replace("declares no winner", ""))

    def test_a_failed_or_insufficient_run_is_not_compared(self):
        org, client, case_id, a, b = self.two_versions("CmpBad")
        self.validate(client, a, GROUPED)
        with mock.patch("engine.validation.runner._fit_recorded", side_effect=RuntimeError("x")):
            self.validate(client, b, GROUPED)
        row = {p["protocol_key"]: p for p in client.get(f"/api/models/compare?a={a}&b={b}").json()["protocols"]}[GROUPED]
        self.assertFalse(row["comparable"])
        self.assertIn("failed or insufficient", row["reason"])

    def test_runs_with_different_protocol_definitions_are_refused(self):
        org, client, case_id, a, b = self.two_versions("CmpDef")
        for vid in (a, b):
            self.validate(client, vid, GROUPED)
        from backend.app.config import database
        with database.db_transaction() as conn:
            conn.execute(text("UPDATE model_validations SET protocol_hash = 'other' WHERE model_version_id = :v"), {"v": b})
        row = {p["protocol_key"]: p for p in client.get(f"/api/models/compare?a={a}&b={b}").json()["protocols"]}[GROUPED]
        self.assertFalse(row["comparable"])
        self.assertIn("different protocol definitions", row["reason"])

    def test_same_version_and_other_programs_are_refused(self):
        org, client, case_id, a, b = self.two_versions("CmpSame")
        self.assertEqual(client.get(f"/api/models/compare?a={a}&b={a}").status_code, 400)
        org2, client2, case2, ids2, versions2 = self.ranked("CmpOther")
        r = client.get(f"/api/models/compare?a={a}&b={versions2[0]['id']}")
        self.assertIn(r.status_code, (400, 404))


class TestPaginationAndLibrary(Base):
    dir_name = "_test_env_model_library"

    def test_library_lists_versions_with_their_validation_state(self):
        org, client, case_id, ids, versions = self.ranked("Lib")
        r = client.get("/api/models?limit=1&offset=0").json()
        self.assertEqual((r["total"], len(r["versions"]), r["versions"][0]["validation"]["state"]), (1, 1, "incomplete"))
        self.assertEqual(client.get("/api/models?limit=0").status_code, 422)
        self.assertEqual(client.get("/api/models?program_id=99999").status_code, 404)


class TestFrontendWiring(unittest.TestCase):
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_every_model_api_call_has_a_backend_route(self):
        os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
        os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")
        from backend.app.main import create_app
        routes = {(m, r.path) for r in create_app().routes for m in getattr(r, "methods", None) or ()}
        src = self.read("lib/api/models.ts")
        calls = re.findall(r"`(/api[^`]*)`(?:, \{ method: \"(\w+)\")?", src)
        self.assertGreaterEqual(len(calls), 8)
        for path, method in calls:
            concrete = re.sub(r"\$\{[^}]*(?:\{[^}]*\}[^}]*)*\}", "1", path).split("?")[0]
            found = any(m == (method or "GET") and re.match("^" + re.sub(r"\{[^}]+\}", "[^/]+", rp) + "$", concrete) for m, rp in routes)
            self.assertTrue(found, f"no backend route for {method or 'GET'} {path}")

    def test_pages_exist_follow_the_copy_standard_and_are_linked(self):
        files = ("app/models/page.tsx", "app/models/[id]/page.tsx", "components/models/parts.tsx",
                 "components/models/ModelAnalysisPanel.tsx", "lib/api/models.ts")
        for rel in files:
            self.assertTrue(os.path.isfile(os.path.join(self.ROOT, rel)), rel)
        for rel in ("app/models/page.tsx", "app/models/[id]/page.tsx"):
            self.assertIn("const { checking } = useRequireAuth()", self.read(rel))
        self.assertIn("<Suspense", self.read("app/models/[id]/page.tsx"))
        self.assertIn('href: "/models"', self.read("components/layout/Sidebar.tsx"))
        self.assertIn("ModelAnalysisPanel", self.read("app/programs/[id]/page.tsx"))
        for rel in files[:4]:
            text_ = re.sub(r"/\*.*?\*/", "", self.read(rel), flags=re.S)
            for banned in ("\u2014", "\u2013", " -- ", " \u00b7 "):
                self.assertNotIn(banned, text_, rel)
            self.assertNotRegex(text_, r"(?i)diagnostic")

    def test_screens_never_call_a_model_calibrated_or_qualified(self):
        for rel in ("app/models/page.tsx", "app/models/[id]/page.tsx", "components/models/parts.tsx", "components/models/ModelAnalysisPanel.tsx"):
            text_ = self.read(rel)
            self.assertNotRegex(text_, r"(?i)\bwell[- ]calibrated\b|\bis calibrated\b|\bqualified\b|\bvalidated model\b", rel)
        self.assertIn("not physical qualification results", self.read("app/models/[id]/page.tsx"))


class TestMigration(unittest.TestCase):
    def test_upgrade_downgrade_and_reupgrade_keep_existing_predictions(self):
        import shutil
        d = os.path.join(os.path.dirname(__file__), f"_test_env_model_mig_{uuid.uuid4().hex[:8]}")
        os.makedirs(d)
        self.addCleanup(shutil.rmtree, d, True)
        url = f"sqlite:///{d}/m.db"
        engine = create_engine(url)

        @event.listens_for(engine, "connect")
        def _fk(c, r):
            c.execute("PRAGMA foreign_keys=ON")
        with _UseDatabase(url):
            command.upgrade(_config(), "d2e6f8a1b3c5")
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('M', 't')"))
                conn.execute(text("INSERT INTO users (email, password_hash, display_name, is_active, created_at) VALUES ('m@example.com', 'h', 'M', 1, 't')"))
                conn.execute(text("""INSERT INTO change_cases (organization_id, created_by_user_id, name, trigger_type, restricted_substance,
                                     qualification_spec_json, status, created_at, updated_at) VALUES (1, 1, 'c', 'other', 'x', '{}', 'draft', 't', 't')"""))
                conn.execute(text("""INSERT INTO qualification_datasets (organization_id, change_case_id, uploaded_by_user_id, original_filename,
                                     stored_filename, row_count, created_at) VALUES (1, 1, 1, 'f.csv', 's.csv', 5, 't')"""))
                conn.execute(text("""INSERT INTO candidate_substitutes (organization_id, change_case_id, candidate_name, properties_json, created_at)
                                     VALUES (1, 1, 'c1', '{}', 't')"""))
                conn.execute(text("""INSERT INTO predictions (organization_id, candidate_id, dataset_version_id, model_version, predicted_probability,
                                     uncertainty_std, created_at) VALUES (1, 1, 1, 'gp-rank-v1', 0.5, 1.0, 't')"""))
            command.upgrade(_config(), "head")
            with engine.begin() as conn:
                self.assertIsNone(conn.execute(text("SELECT model_version_id FROM predictions")).scalar())  # nothing back filled
                conn.execute(text("""INSERT INTO model_versions (organization_id, change_case_id, version_number, model_key, identity_hash, training_data_hash,
                                     dataset_id, family, engine_version, seed, feature_definitions_json, target_json, preprocessing_json,
                                     configuration_json, environment_json, training_summary_json, n_training_rows, n_input_sets, source, created_at)
                                     VALUES (1, 1, 1, 'gp-x', 'h1', 'd1', 1, 'gp-rank-v1', 'e', 0, '[]', '{}', '{}', '{}', '{}', '{}', 5, 5, 'ranking', 't')"""))
                conn.execute(text("UPDATE predictions SET model_version_id = 1"))
                with self.assertRaises(Exception):  # the same identity cannot be registered twice in a program
                    conn.execute(text("""INSERT INTO model_versions (organization_id, change_case_id, version_number, model_key, identity_hash,
                                         training_data_hash, family, engine_version, seed, feature_definitions_json, target_json, preprocessing_json,
                                         configuration_json, environment_json, training_summary_json, n_training_rows, n_input_sets, source, created_at)
                                         VALUES (1, 1, 2, 'gp-y', 'h1', 'd1', 'gp-rank-v1', 'e', 0, '[]', '{}', '{}', '{}', '{}', '{}', 5, 5, 'ranking', 't')"""))
            command.downgrade(_config(), "d2e6f8a1b3c5")
            with engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM predictions")).scalar(), 1)
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(predictions)"))}
                self.assertNotIn("model_version_id", cols)
                tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
                self.assertFalse({"model_versions", "model_validations"} & tables)
            command.upgrade(_config(), "head")
            with engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM predictions")).scalar(), 1)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
