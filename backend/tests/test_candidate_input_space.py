"""Level 5 correction: candidate decision support uses the multivariate applicability diagnostic.
Run:  python3 -m unittest backend.tests.test_candidate_input_space -v"""
import os
import random
import re
import unittest
from unittest import mock

import numpy as np
from sqlalchemy import text

from backend.app.services import change_case_rules as R
from backend.app.services import ranking_evidence_rules as RER
from backend.tests import evidence_fixtures as F
from backend.tests.test_model_validation_center import Base, HEAD
from engine.validation import applicability as A

WITHIN, NEAR, OUTSIDE = R.DOMAIN_WITHIN, R.DOMAIN_NEAR_EDGE, R.DOMAIN_OUTSIDE


def cov(status):
    return {"status": status, "features": {}}


def space(cls, reason=None):
    return {"status": "not_assessed" if cls == "not_assessed" else "assessed", "class": cls, "reason": reason}


def clustered_rows(shift=0.0, seed=3):
    """Two tight clusters at opposite corners: every single feature spans a wide range, but the corners in between are
    empty. Rows are (ratio, temp, hours)."""
    rnd = random.Random(seed)
    rows = []
    for i in range(12):
        rows.append((round(0.08 + shift + rnd.uniform(0, 0.02), 4), round(140 + rnd.uniform(0, 10), 1), round(100 + 30 * i + rnd.uniform(0, 5), 1)))
    for i in range(12):
        rows.append((round(0.18 + shift + rnd.uniform(0, 0.02), 4), round(190 + rnd.uniform(0, 10), 1), round(150 + 30 * i + rnd.uniform(0, 5), 1)))
    return rows


def reference_of(rows):
    X = np.array([[r[0], r[1]] for r in rows], dtype=float)
    return X, A.build_reference(X)


def find_point(rows, wanted):
    """A point inside the bounding box of every feature whose class, by the Level 5 calculation, is `wanted`."""
    X, ref = reference_of(rows)
    for ratio in np.linspace(X[:, 0].min(), X[:, 0].max(), 41):
        for temp in np.linspace(X[:, 1].min(), X[:, 1].max(), 41):
            found = A.assess_candidate(np.array([ratio, temp]), X, ref)
            if found["class"] == wanted:
                return round(float(ratio), 4), round(float(temp), 1)
    raise AssertionError(f"no point of class {wanted} inside the ranges")


class TestDecisionMatrix(unittest.TestCase):
    def status(self, domain, cls, reason=None):
        return R.derive_decision_support(cov(domain), True, space(cls, reason))

    def test_within_range_combinations(self):
        self.assertEqual(self.status(WITHIN, "within_training_spacing")["status"], "within_data_range")
        self.assertEqual(self.status(WITHIN, "sparse_region")["status"], "caution")
        self.assertEqual(self.status(WITHIN, "isolated")["status"], "requires_validation")
        d = self.status(WITHIN, "not_assessed", "Only 7 distinct training input sets.")
        self.assertEqual((d["status"], d["label"]), ("not_assessed", "Not assessed"))
        self.assertIn("not treated as supported", d["statement"])
        self.assertIn("Only 7 distinct training input sets", d["statement"])

    def test_near_edge_and_outside_keep_or_raise_caution_never_lower_it(self):
        self.assertEqual(self.status(NEAR, "within_training_spacing")["status"], "caution")
        self.assertEqual(self.status(NEAR, "sparse_region")["status"], "caution")
        self.assertEqual(self.status(NEAR, "isolated")["status"], "requires_validation")
        self.assertEqual(self.status(NEAR, "not_assessed", "x")["status"], "caution")
        for cls in ("within_training_spacing", "sparse_region", "isolated", "not_assessed"):
            self.assertEqual(self.status(OUTSIDE, cls)["status"], "requires_validation", cls)

    def test_an_unrecognised_or_missing_class_is_never_supported(self):
        self.assertEqual(R.derive_decision_support(cov(WITHIN), True, {"class": "weird"})["status"], "not_assessed")
        self.assertEqual(R.derive_decision_support(cov(WITHIN), True, {})["status"], "not_assessed")
        self.assertEqual(R.derive_decision_support(None, True, space("within_training_spacing"))["status"], "requires_validation")

    def test_the_range_check_keeps_its_own_result_and_the_limits_stay(self):
        d = self.status(WITHIN, "isolated")
        self.assertEqual((d["range_check_status"], d["domain_status"], d["input_space_class"]), ("within_data_range", WITHIN, "isolated"))
        self.assertEqual(d["basis"], R.DECISION_SUPPORT_BASIS_INTEGRATED)
        self.assertIn("joint_input_space_coverage", d["not_established"])
        self.assertIn("qualification", d["not_established"])

    def test_without_an_input_space_the_result_is_exactly_what_it_was(self):
        d = R.derive_decision_support(cov(WITHIN), True)
        self.assertEqual((d["status"], d["basis"], d["scope"]), ("within_data_range", "historical_domain_coverage", "per_feature_range_check"))
        self.assertIsNone(R.derive_decision_support(cov(WITHIN), False, space("isolated")))

    def test_ranking_order_still_follows_the_range_check_only(self):
        rows = [
            {"id": 1, "predicted_probability": 0.9, "predicted_value": 1.0, "decision_support": self.status(WITHIN, "isolated")},
            {"id": 2, "predicted_probability": 0.8, "predicted_value": 1.0, "decision_support": self.status(WITHIN, "not_assessed", "x")},
            {"id": 3, "predicted_probability": 0.7, "predicted_value": 1.0, "decision_support": self.status(OUTSIDE, "within_training_spacing")},
        ]
        out = RER.evidence_ranking(rows, "maximize")
        self.assertEqual(out["order"][:2], [1, 2])
        self.assertEqual(out["by_id"][1]["status"], RER.STATUS_RANKED)
        self.assertEqual(out["by_id"][2]["status"], RER.STATUS_RANKED)
        self.assertEqual(out["by_id"][3]["status"], RER.STATUS_NOT_RANKED)
        # a signal with no range_check_status still fails safe exactly as before
        self.assertEqual(RER.evidence_ranking([{"id": 9, "predicted_probability": .5, "predicted_value": 1, "decision_support": {"status": "not_assessed"}}], "maximize")["by_id"][9]["status"], RER.STATUS_NOT_RANKED)


class TestCandidateApi(Base):
    dir_name = "_test_env_candidate_space"

    def case(self, label, rows=None, extra=(), rank=True):
        rows = rows or clustered_rows()
        org, client, case_id, ids = self.case_with_evidence(label, candidates=1)
        self.assertEqual(self.upload(client, case_id, "scatter.csv", F.csv_bytes(HEAD, rows)).status_code, 201)
        made = {}
        for name, (ratio, temp) in extra:
            r = client.post(f"/api/change-cases/{case_id}/candidates", json={"name": name, "features": {"crosslinker_ratio": ratio, "cure_temp_c": temp}})
            self.assertEqual(r.status_code, 201, r.text)
            made[name] = r.json()["id"]
        results = None
        if rank:
            rr = client.post(f"/api/change-cases/{case_id}/rank")
            self.assertEqual(rr.status_code, 200, rr.text)
            results = {x["candidate_name"]: x for x in rr.json()}
        return org, client, case_id, made, results

    def listing(self, client, case_id):
        return {c["candidate_name"]: c for c in client.get(f"/api/change-cases/{case_id}/candidates").json()}

    def test_inside_every_range_but_isolated_requires_validation_and_is_still_ranked(self):
        rows = clustered_rows()
        corner = (0.09, 195.0)  # inside both ranges, in the empty corner between the two clusters
        org, client, case_id, made, results = self.case("Iso", rows, [("Corner", corner)])
        c = self.listing(client, case_id)["Corner"]
        self.assertEqual(c["domain_coverage"]["status"], "within_historical_domain")  # the per feature check is unchanged
        s = c["input_space_applicability"]
        self.assertEqual((s["status"], s["class"]), ("assessed", "isolated"))
        self.assertGreater(s["nearest_distance"], s["thresholds"]["isolated_above"])
        ds = c["decision_support"]
        self.assertEqual((ds["status"], ds["label"], ds["range_check_status"]), ("requires_validation", "Requires validation", "within_data_range"))
        self.assertIn("isolated", ds["statement"])
        # the order is untouched: this candidate is still ranked on its range check
        self.assertEqual(results["Corner"]["ranking_status"], "ranked")
        self.assertIsNotNone(results["Corner"]["rank"])

    def test_sparse_is_caution_and_within_spacing_is_supported(self):
        rows = clustered_rows()
        sparse = find_point(rows, "sparse_region")
        inside = find_point(rows, "within_training_spacing")
        org, client, case_id, made, results = self.case("Sparse", rows, [("Sparse", sparse), ("Inside", inside)])
        listing = self.listing(client, case_id)
        s = listing["Sparse"]
        self.assertEqual((s["input_space_applicability"]["class"], s["decision_support"]["status"], s["decision_support"]["label"]), ("sparse_region", "caution", "Caution"))
        self.assertEqual(s["domain_coverage"]["status"], "within_historical_domain")
        i = listing["Inside"]
        self.assertEqual((i["input_space_applicability"]["class"], i["decision_support"]["status"]), ("within_training_spacing", "within_data_range"))
        self.assertIn("spacing", i["decision_support"]["statement"])
        self.assertIn("not physical evidence", i["decision_support"]["statement"])

    def test_outside_a_feature_range_still_requires_validation(self):
        org, client, case_id, made, results = self.case("Out", clustered_rows(), [("Far", (0.5, 400.0))])
        c = self.listing(client, case_id)["Far"]
        self.assertEqual(c["domain_coverage"]["status"], "outside_historical_domain")
        self.assertEqual(c["decision_support"]["status"], "requires_validation")
        self.assertEqual(c["input_space_applicability"]["class"], "isolated")  # still reported, on its own
        self.assertEqual(results["Far"]["ranking_status"], "not_ranked_insufficient_evidence")

    def test_too_few_distinct_training_input_sets_is_not_assessed(self):
        rows = clustered_rows()[:7] + []
        org, client, case_id, made, results = self.case("Few", rows, [("Mid", (0.1, 150.0))], rank=False)
        rr = client.post(f"/api/change-cases/{case_id}/rank")
        if rr.status_code != 200:  # ranking itself can refuse tiny evidence; then test the calculation directly
            from backend.app.repositories import change_case_repo, qualification_dataset_repo
            from backend.app.services import model_validation_service as svc
            case = change_case_repo.get_change_case(org, case_id)
            ds = qualification_dataset_repo.get_latest_dataset_for_change_case(org, case_id)
            version, _ = svc.register(org, case, ds, None, "registered")
            out = svc.candidate_input_space(org, case, version["id"], ds["id"], {"crosslinker_ratio": 0.1, "cure_temp_c": 150.0}, {})
            self.assertEqual((out["status"], out["class"]), ("not_assessed", "not_assessed"))
            self.assertIn("Only 7 distinct training input sets", out["reason"])
            self.assertIsNone(out["thresholds"])
            self.assertEqual(R.derive_decision_support(cov(WITHIN), True, out)["status"], "not_assessed")
            return
        c = self.listing(client, case_id)["Mid"]
        self.assertEqual(c["input_space_applicability"]["status"], "not_assessed")
        self.assertIn("distinct training input sets", c["input_space_applicability"]["reason"])
        self.assertEqual(c["decision_support"]["status"], "not_assessed")
        self.assertEqual(c["decision_support"]["label"], "Not assessed")

    def test_a_missing_model_version_is_not_assessed_and_never_supported(self):
        rows = clustered_rows()
        org, client, case_id, made, _ = self.case("NoVer", rows, [("Inside", find_point(rows, "within_training_spacing")), ("Far", (0.5, 400.0))], rank=False)
        with mock.patch("backend.app.services.model_validation_service.register", side_effect=RuntimeError("x")):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        listing = self.listing(client, case_id)
        c = listing["Inside"]
        self.assertIsNone(c["latest_prediction"]["model_version_id"])
        self.assertEqual(c["input_space_applicability"]["status"], "not_assessed")
        self.assertIn("no registered model version", c["input_space_applicability"]["reason"])
        self.assertEqual((c["decision_support"]["status"], c["decision_support"]["label"]), ("not_assessed", "Not assessed"))
        self.assertEqual(listing["Far"]["decision_support"]["status"], "requires_validation")  # the range check still decides

    def test_a_stale_prediction_is_measured_against_its_own_version_never_newer_evidence(self):
        rows_a, rows_b = clustered_rows(), clustered_rows(shift=0.5, seed=9)
        point = find_point(rows_a, "within_training_spacing")
        org, client, case_id, made, _ = self.case("Stale", rows_a, [("P", point)])
        before = self.listing(client, case_id)["P"]["input_space_applicability"]
        vid_a = before["model_version_id"]
        self.assertEqual(self.upload(client, case_id, "newer.csv", F.csv_bytes(HEAD, rows_b)).status_code, 201)
        after = self.listing(client, case_id)["P"]
        self.assertTrue(after["prediction_stale"])
        s = after["input_space_applicability"]
        self.assertEqual((s["model_version_id"], s["class"]), (vid_a, before["class"]))
        self.assertEqual(s["thresholds"], before["thresholds"])
        training_a = {(r[0], r[1]) for r in rows_a}
        nearest = s["nearest_training_input_set"]
        self.assertIn((nearest["crosslinker_ratio"], nearest["cure_temp_c"]), training_a)  # a row of the OLD evidence
        self.assertEqual(client.get(f"/api/models/{vid_a}").json()["is_current"], False)
        client.post(f"/api/change-cases/{case_id}/rank")  # only a new ranking moves it to the newer evidence
        fresh = self.listing(client, case_id)["P"]["input_space_applicability"]
        self.assertNotEqual(fresh["model_version_id"], vid_a)

    def test_tampered_evidence_and_mismatched_versions_are_not_assessed(self):
        rows = clustered_rows()
        point = find_point(rows, "within_training_spacing")
        org, client, case_id, made, _ = self.case("Tamper2", rows, [("P", point)])
        from backend.app.config import database
        pred = self.listing(client, case_id)["P"]["latest_prediction"]
        # a version built from different evidence than the prediction used
        self.assertEqual(self.upload(client, case_id, "other.csv", F.csv_bytes(HEAD, clustered_rows(shift=0.4, seed=5))).status_code, 201)
        other = client.post(f"/api/change-cases/{case_id}/models/register").json()["version"]["id"]
        with database.db_transaction() as conn:
            conn.execute(text("UPDATE predictions SET model_version_id = :v WHERE id = :p"), {"v": other, "p": pred["id"]})
        s = self.listing(client, case_id)["P"]["input_space_applicability"]
        self.assertEqual(s["status"], "not_assessed")
        self.assertIn("different evidence", s["reason"])
        self.assertEqual(self.listing(client, case_id)["P"]["decision_support"]["status"], "not_assessed")
        # evidence that no longer reproduces its version
        with database.db_transaction() as conn:
            conn.execute(text("UPDATE predictions SET model_version_id = :v WHERE id = :p"), {"v": pred["model_version_id"], "p": pred["id"]})
            conn.execute(text("UPDATE qualification_experiments SET target_value = target_value + 1 WHERE qualification_dataset_id = :d"), {"d": pred["dataset_version_id"]})
        s = self.listing(client, case_id)["P"]["input_space_applicability"]
        self.assertEqual(s["status"], "not_assessed")
        self.assertIn("no longer reproduces", s["reason"])

    def test_candidate_endpoint_ranking_result_and_applicability_tab_agree(self):
        rows = clustered_rows()
        extra = [("Corner", (0.09, 195.0)), ("Sparse", find_point(rows, "sparse_region")), ("Inside", find_point(rows, "within_training_spacing"))]
        org, client, case_id, made, results = self.case("Agree", rows, extra)
        listing = self.listing(client, case_id)
        vid = listing["Corner"]["input_space_applicability"]["model_version_id"]
        tab = {c["name"]: c for c in client.get(f"/api/models/{vid}/applicability").json()["candidates"]}
        ref = client.get(f"/api/models/{vid}/applicability").json()["reference"]
        for name in ("Corner", "Sparse", "Inside"):
            api = listing[name]["input_space_applicability"]
            self.assertEqual(api["class"], tab[name]["class"], name)
            self.assertAlmostEqual(api["nearest_distance"], tab[name]["nearest_distance"], places=12)
            self.assertEqual(api["nearest_training_input_set"], tab[name]["nearest_training_input_set"])
            self.assertEqual(api["nearest_training_rows"], tab[name]["nearest_training_rows"])
            self.assertEqual(api["thresholds"], ref)
            self.assertEqual(results[name]["input_space_applicability"], api)  # the ranking response says the same
            self.assertEqual(results[name]["decision_support"]["status"], listing[name]["decision_support"]["status"])

    def test_values_come_from_the_shared_engine_calculation(self):
        rows = clustered_rows()
        org, client, case_id, made, _ = self.case("Engine", rows, [("Corner", (0.09, 195.0))])
        api = self.listing(client, case_id)["Corner"]["input_space_applicability"]
        X, ref = reference_of(rows)
        found = A.assess_candidate(np.array([0.09, 195.0]), X, ref)
        self.assertAlmostEqual(api["nearest_distance"], found["nearest_distance"], places=12)
        self.assertEqual(api["class"], found["class"])
        self.assertEqual(api["thresholds"], ref["limits"])

    def test_prediction_numbers_are_not_touched(self):
        rows = clustered_rows()
        org, client, case_id, made, results = self.case("Numbers", rows, [("Corner", (0.09, 195.0))])
        stored = self.listing(client, case_id)["Corner"]["latest_prediction"]
        r = results["Corner"]
        self.assertEqual((stored["predicted_probability"], stored["uncertainty_std"], stored["predicted_value"]),
                         (r["predicted_probability"], r["uncertainty_std"], r["predicted_value"]))

    def test_the_report_explanation_describes_both_checks(self):
        from backend.app.services.report_builder import DECISION_SUPPORT_EXPLANATION as text_
        for phrase in ("checked one feature at a time", "nearest training input set", "Not assessed", "not repeated in this report",
                       "does not show that the combination of inputs is covered by the historical data"):
            self.assertIn(phrase, text_)


class TestFrontendIntegration(unittest.TestCase):
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_candidates_card_shows_both_checks_and_links_to_the_applicability_tab(self):
        page = self.read("app/programs/[id]/page.tsx")
        self.assertIn("Historical domain", page)
        self.assertIn("Combination of inputs", page)
        self.assertIn("/models/${c.input_space_applicability.model_version_id}?tab=applicability", page)
        self.assertIn('ds.status === "not_assessed"', page)

    def test_labels_cover_exactly_the_backend_classes(self):
        page = self.read("app/programs/[id]/page.tsx")
        block = re.search(r"INPUT_SPACE_LABEL[^=]*=\s*\{(.*?)\};", page, re.S).group(1)
        self.assertEqual(set(re.findall(r"(\w+):", block)), set(A.CLASSES))
        models = self.read("components/models/parts.tsx")
        for label in re.findall(r'"([A-Z][^"]+)"', block):
            self.assertIn(label, models)  # the same wording as the Applicability tab

    def test_types_carry_the_new_fields(self):
        ts = self.read("lib/api/change-cases.ts")
        for needle in ("InputSpaceApplicability", "range_check_status", '"not_assessed"', "nearest_training_input_set", "thresholds"):
            self.assertIn(needle, ts)
        self.assertEqual(len(re.findall(r"input_space_applicability\?: InputSpaceApplicability \| null", ts)), 2)

    def test_copy_follows_the_standard(self):
        page = re.sub(r"/\*.*?\*/", "", self.read("app/programs/[id]/page.tsx"), flags=re.S)
        for banned in ("\u2014", "\u2013", " -- "):
            self.assertNotIn(banned, page)


if __name__ == "__main__":
    unittest.main()
