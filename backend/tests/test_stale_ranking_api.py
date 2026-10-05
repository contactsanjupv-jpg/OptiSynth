"""
Stale-ranking UX: a ranking is STALE when the dataset it was generated from is no
longer the current (newest) evidence. Detection is display-only -- it never changes
which dataset anything is computed from.

Lifecycle proven here, through the real HTTP path:
  rank on evidence A  ->  not stale
  accept newer evidence B  ->  rankings flagged STALE with the exact notice, still
                              tied to A (provenance unchanged), A = superseded, B = current
  re-run ranking  ->  stale warning gone, predictions now reference B, coverage is
                      evaluated against B's ranges

Run:  python3 -m unittest backend.tests.test_stale_ranking_api -v
"""
import json
import unittest

from backend.tests import evidence_fixtures as F
from backend.tests._api_base import CONFIRM_ALIASES, IsolatedApiTestCase

OLD_NAME = "old_history.csv"
NEW_NAME = "new_evidence.xlsx"
NOTICE = (
    f"This ranking was generated from {OLD_NAME}. Current evidence is {NEW_NAME}. "
    "Re-run ranking to use the current evidence."
)


class TestStaleRankingLifecycle(IsolatedApiTestCase):
    dir_name = "_test_env_stale"

    # A = 14 rows: crosslinker 0.08..0.145, cure 140..179.   B = 12 rows: crosslinker 0.08..0.135, cure 140..173.
    def _setup(self, label):
        org_id, client, case_id = self.setup_case(label)
        self.paid(org_id)
        up_a = self.upload(client, case_id, OLD_NAME, F.csv_bytes(F.CANON_HEADERS, F.base_rows(14)))
        self.assertEqual(up_a.status_code, 201, up_a.text)
        for name, ratio, temp in (("PolyGuard", 0.145, 170), ("EcoShield", 0.16, 178)):
            r = client.post(f"/api/change-cases/{case_id}/candidates",
                            json={"name": name, "features": {"crosslinker_ratio": ratio, "cure_temp_c": temp}})
            self.assertEqual(r.status_code, 201, r.text)
        return org_id, client, case_id, up_a.json()["dataset_id"]

    def _rank(self, client, case_id):
        r = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 200, r.text)
        return {x["candidate_name"]: x for x in r.json()}

    def _candidates(self, client, case_id):
        r = client.get(f"/api/change-cases/{case_id}/candidates")
        self.assertEqual(r.status_code, 200, r.text)
        return {c["candidate_name"]: c for c in r.json()}

    def _accept_newer(self, client, case_id):
        up = self.upload(client, case_id, NEW_NAME, F.xlsx_alt_headers(), CONFIRM_ALIASES)
        self.assertEqual(up.status_code, 201, up.text)
        return up.json()["dataset_id"]

    def _check(self, client, case_id, code):
        s = client.get(f"/api/change-cases/{case_id}/sufficiency").json()
        return s, next((c for c in s["checks"] if c["code"] == code), None)

    # ------------------------------------------------------------------
    def test_candidates_without_a_prediction_are_neither_stale_nor_fresh(self):
        org_id, client, case_id, a_id = self._setup("StaleNone")
        for c in self._candidates(client, case_id).values():
            self.assertIsNone(c["prediction_stale"])
            self.assertIsNone(c["stale_notice"])
            self.assertIsNone(c["prediction_dataset"])
            self.assertEqual(c["current_dataset"], {"id": a_id, "original_filename": OLD_NAME})
        _, chk = self._check(client, case_id, "PREDICTIONS_CURRENT")
        self.assertIsNone(chk)  # nothing ranked yet: nothing to say

    def test_a_fresh_ranking_is_not_stale_and_names_its_dataset(self):
        org_id, client, case_id, a_id = self._setup("StaleFresh")
        self._rank(client, case_id)
        for c in self._candidates(client, case_id).values():
            self.assertIs(c["prediction_stale"], False)
            self.assertIsNone(c["stale_notice"])
            self.assertEqual(c["prediction_dataset"], {"id": a_id, "original_filename": OLD_NAME})
            self.assertEqual(c["prediction_dataset"], c["current_dataset"])
            self.assertEqual(c["latest_prediction"]["dataset_version_id"], a_id)
        _, chk = self._check(client, case_id, "PREDICTIONS_CURRENT")
        self.assertEqual(chk["status"], "pass")
        inv = client.get(f"/api/change-cases/{case_id}/evidence").json()
        self.assertEqual([(i["status"], i["used_by_displayed_rankings"]) for i in inv], [("current", True)])

    def test_accepting_newer_evidence_marks_existing_rankings_stale_without_changing_them(self):
        org_id, client, case_id, a_id = self._setup("StaleAppears")
        before = self._rank(client, case_id)
        b_id = self._accept_newer(client, case_id)
        after = self._candidates(client, case_id)
        for name, c in after.items():
            self.assertIs(c["prediction_stale"], True, name)
            self.assertEqual(c["stale_notice"], NOTICE, name)
            self.assertEqual(c["prediction_dataset"], {"id": a_id, "original_filename": OLD_NAME})
            self.assertEqual(c["current_dataset"], {"id": b_id, "original_filename": NEW_NAME})
            # provenance is untouched: the prediction still references the dataset it came from,
            # with the same numbers it had
            self.assertEqual(c["latest_prediction"]["dataset_version_id"], a_id)
            self.assertEqual(c["latest_prediction"]["predicted_probability"], before[name]["predicted_probability"])
            self.assertEqual(c["latest_prediction"]["uncertainty_std"], before[name]["uncertainty_std"])
        # ...and coverage is still judged against A (existing, intended behaviour)
        self.assertEqual(after["PolyGuard"]["domain_coverage"]["status"], "within_historical_domain")
        self.assertEqual(after["PolyGuard"]["decision_support"]["status"], "evidence_supported")

    def test_a_candidate_added_after_the_new_upload_is_not_stale_just_unranked(self):
        org_id, client, case_id, a_id = self._setup("StaleNew")
        self._rank(client, case_id)
        self._accept_newer(client, case_id)
        client.post(f"/api/change-cases/{case_id}/candidates",
                    json={"name": "Later", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}})
        c = self._candidates(client, case_id)
        self.assertIsNone(c["Later"]["prediction_stale"])
        self.assertIsNone(c["Later"]["stale_notice"])
        self.assertIs(c["PolyGuard"]["prediction_stale"], True)

    def test_inventory_labels_current_and_superseded_unambiguously(self):
        org_id, client, case_id, a_id = self._setup("StaleLabels")
        self._rank(client, case_id)
        b_id = self._accept_newer(client, case_id)
        inv = {i["id"]: i for i in client.get(f"/api/change-cases/{case_id}/evidence").json()}
        self.assertEqual((inv[b_id]["status"], inv[b_id]["is_current"], inv[b_id]["used_by_displayed_rankings"]),
                         ("current", True, False))
        self.assertEqual((inv[a_id]["status"], inv[a_id]["is_current"], inv[a_id]["used_by_displayed_rankings"]),
                         ("superseded", False, True))  # superseded, yet the rankings on screen still rest on it

    def test_sufficiency_shows_the_stale_warning_but_does_not_block_ranking(self):
        org_id, client, case_id, a_id = self._setup("StaleSuff")
        self._rank(client, case_id)
        self._accept_newer(client, case_id)
        s, chk = self._check(client, case_id, "PREDICTIONS_CURRENT")
        self.assertEqual(chk["status"], "warn")
        self.assertIn(NOTICE, chk["detail"])
        self.assertIn("PolyGuard", chk["detail"])
        self.assertTrue(s["can_rank"])  # informational: re-ranking must remain possible

    def test_report_carries_the_notice_and_stays_tied_to_the_original_dataset(self):
        org_id, client, case_id, a_id = self._setup("StaleReport")
        self._rank(client, case_id)
        b_id = self._accept_newer(client, case_id)
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertIn("NOTICE -- NEWER EVIDENCE EXISTS.", text)
        self.assertIn(NOTICE, text)
        self.assertIn(f"Version {a_id}: {OLD_NAME}", text)      # still describes the data it was ranked on
        self.assertNotIn(f"Version {b_id}: {NEW_NAME}", text)

    def test_rerunning_ranking_clears_stale_and_moves_everything_to_the_current_dataset(self):
        org_id, client, case_id, a_id = self._setup("StaleCleared")
        self._rank(client, case_id)
        b_id = self._accept_newer(client, case_id)
        self.assertTrue(all(c["prediction_stale"] for c in self._candidates(client, case_id).values()))

        reranked = self._rank(client, case_id)

        # the stale marker is gone everywhere
        cands = self._candidates(client, case_id)
        for name, c in cands.items():
            self.assertIs(c["prediction_stale"], False, name)
            self.assertIsNone(c["stale_notice"], name)
            self.assertEqual(c["prediction_dataset"], {"id": b_id, "original_filename": NEW_NAME}, name)
            self.assertEqual(c["latest_prediction"]["dataset_version_id"], b_id, name)
        _, chk = self._check(client, case_id, "PREDICTIONS_CURRENT")
        self.assertEqual(chk["status"], "pass")
        inv = {i["id"]: i for i in client.get(f"/api/change-cases/{case_id}/evidence").json()}
        self.assertEqual((inv[b_id]["status"], inv[b_id]["used_by_displayed_rankings"]), ("current", True))
        self.assertEqual((inv[a_id]["status"], inv[a_id]["used_by_displayed_rankings"]), ("superseded", False))

        # PolyGuard (crosslinker 0.145) is now judged against B's range (max 0.135), not A's (max 0.145)
        for source in (reranked["PolyGuard"], cands["PolyGuard"]):
            cov = source["domain_coverage"]
            self.assertEqual(cov["status"], "outside_historical_domain")
            f = cov["features"]["crosslinker_ratio"]
            self.assertEqual((f["historical_min"], f["historical_max"]), (0.08, 0.135))
            self.assertEqual(f["status"], "outside_historical_domain")
            self.assertEqual(source["decision_support"]["status"], "requires_validation")
        self.assertEqual(cands["EcoShield"]["domain_coverage"]["status"], "outside_historical_domain")

        # the report now describes B and carries no notice
        text = self.docx_text(client.post(f"/api/change-cases/{case_id}/report").content)
        self.assertNotIn("NEWER EVIDENCE EXISTS", text)
        self.assertIn(f"Version {b_id}: {NEW_NAME}", text)

    def test_history_is_preserved_old_predictions_are_kept_new_ones_added(self):
        org_id, client, case_id, a_id = self._setup("StaleHistory")
        self._rank(client, case_id)
        b_id = self._accept_newer(client, case_id)
        self._rank(client, case_id)
        from backend.app.config.database import db_connection
        from sqlalchemy import text
        with db_connection() as conn:
            rows = conn.execute(text(
                "SELECT p.dataset_version_id FROM predictions p JOIN candidate_substitutes c ON c.id = p.candidate_id "
                "WHERE c.change_case_id = :cid"), {"cid": case_id}).all()
            n_old_rows = conn.execute(text(
                "SELECT COUNT(*) FROM qualification_experiments WHERE qualification_dataset_id = :d"), {"d": a_id}).scalar()
        self.assertEqual(sorted(r[0] for r in rows), [a_id, a_id, b_id, b_id])  # 2 candidates x 2 rankings
        self.assertEqual(n_old_rows, 14)  # the superseded evidence rows are untouched

    def test_ranking_input_is_still_only_the_newest_dataset(self):
        """Stale detection must not alter dataset selection: a third upload is what the next ranking uses."""
        org_id, client, case_id, a_id = self._setup("StaleSelect")
        self._rank(client, case_id)
        self._accept_newer(client, case_id)
        c_up = self.upload(client, case_id, "third.csv", F.csv_bytes(F.CANON_HEADERS, F.base_rows(16)))
        c_id = c_up.json()["dataset_id"]
        self._rank(client, case_id)
        c = self._candidates(client, case_id)["PolyGuard"]
        self.assertEqual(c["latest_prediction"]["dataset_version_id"], c_id)
        self.assertIs(c["prediction_stale"], False)
        self.assertEqual(c["domain_coverage"]["features"]["crosslinker_ratio"]["historical_max"], 0.155)


if __name__ == "__main__":
    unittest.main()
