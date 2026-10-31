"""Report download filename. Run:  python3 -m unittest backend.tests.test_report_filename -v"""
import unittest
from unittest import mock

from backend.app.services import report_filename as rf
from backend.tests import evidence_fixtures as F
from backend.tests._api_base import IsolatedApiTestCase

TITLE = "Guangxi mix ledger - 28d compressive strength 35 MPa"


class TestFilenameHelpers(unittest.TestCase):
    def test_descriptive_name_matches_the_example(self):
        rid = rf.run_identifier("2026-10-29T20:42:17.123456+00:00")
        self.assertEqual(rid, "20261029T204217Z")
        self.assertEqual(rf.build_report_filename(TITLE, 15, rid),
                         "Guangxi_mix_ledger_28d_compressive_strength_35_MPa_case_15_run_20261029T204217Z.docx")

    def test_run_identifier_converts_to_utc_and_rejects_junk(self):
        self.assertEqual(rf.run_identifier("2026-10-29T22:42:17+02:00"), "20261029T204217Z")
        self.assertEqual(rf.run_identifier("2026-10-29 20:42:17"), "20261029T204217Z")
        for bad in (None, "", "not a date"):
            self.assertIsNone(rf.run_identifier(bad))

    def test_distinct_runs_give_distinct_names_and_latest_wins(self):
        a, b = "2026-10-29T20:42:17+00:00", "2026-10-29T21:00:00+00:00"
        self.assertNotEqual(rf.build_report_filename(TITLE, 15, rf.run_identifier(a)),
                            rf.build_report_filename(TITLE, 15, rf.run_identifier(b)))
        self.assertEqual(rf.latest_run_identifier([a, b, a]), "20261029T210000Z")
        self.assertIsNone(rf.latest_run_identifier([]))

    def test_same_title_different_ids_differ(self):
        self.assertNotEqual(rf.build_report_filename("T", 1, "x"), rf.build_report_filename("T", 2, "x"))

    def test_unsafe_characters_are_removed(self):
        name = rf.build_report_filename('../../etc/pass\\wd:*?"<>|\r\n\x00  a   b..', 7, "20261029T204217Z")
        self.assertRegex(name, r"^[\w]+\.docx$")
        self.assertNotIn("..", name)
        self.assertTrue(name.startswith("etc_pass_wd_a_b_case_7_run_"))

    def test_length_is_capped_and_extension_kept(self):
        name = rf.build_report_filename("x" * 500, 15, "20261029T204217Z")
        self.assertLessEqual(len(name), 130)
        self.assertTrue(name.endswith(".docx"))

    def test_missing_title_or_run_fall_back(self):
        self.assertEqual(rf.build_report_filename(None, 3, None), "qualification_diagnostic_case_3_run_none.docx")
        self.assertEqual(rf.build_report_filename("///", 3, None), "qualification_diagnostic_case_3_run_none.docx")

    def test_header_ascii_and_utf8_forms(self):
        h = rf.content_disposition(TITLE, 15, "20261029T204217Z")
        self.assertEqual(h, 'attachment; filename="Guangxi_mix_ledger_28d_compressive_strength_35_MPa_case_15_run_20261029T204217Z.docx"')
        h = rf.content_disposition("混凝土 Café", 15, "20261029T204217Z")
        h.encode("latin-1")
        self.assertIn('filename="Cafe_case_15_run_20261029T204217Z.docx"', h)
        self.assertIn("filename*=UTF-8''%E6%B7%B7%E5%87%9D%E5%9C%9F_Caf%C3%A9_case_15", h)
        h = rf.content_disposition("混凝土", 4, None)  # all-non-ASCII title: ASCII part falls back
        self.assertIn('filename="qualification_diagnostic_case_4_run_none.docx"', h)
        self.assertIn("filename*=UTF-8''", h)


class TestReportDownloadHeader(IsolatedApiTestCase):
    dir_name = "_test_env_reportname"

    def _case(self, label):
        org_id, client, case_id = self.setup_case(label)
        self.paid(org_id)
        data = F.csv_bytes(F.CANON_HEADERS, [(a, b, 100 + 40 * i) for i, (a, b, _) in enumerate(F.base_rows(14))])
        self.assertEqual(self.upload(client, case_id, "d.csv", data).status_code, 201)
        r = client.post(f"/api/change-cases/{case_id}/candidates",
                        json={"name": "Mid", "features": {"crosslinker_ratio": 0.11, "cure_temp_c": 155}})
        self.assertEqual(r.status_code, 201, r.text)
        return client, case_id

    def _rank_at(self, client, case_id, stamp):
        with mock.patch("backend.app.repositories.candidate_repo.now_iso", return_value=stamp):
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)

    def test_header_has_title_id_and_run(self):
        client, case_id = self._case("FnRun")
        self._rank_at(client, case_id, "2026-10-29T20:42:17.5+00:00")
        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-disposition"],
                         f'attachment; filename="case_case_{case_id}_run_20261029T204217Z.docx"')
        self.assertIn("wordprocessingml", r.headers["content-type"])

    def test_new_ranking_run_gives_a_new_name(self):
        client, case_id = self._case("FnTwo")
        self._rank_at(client, case_id, "2026-10-29T20:42:17+00:00")
        first = client.post(f"/api/change-cases/{case_id}/report").headers["content-disposition"]
        self._rank_at(client, case_id, "2026-10-29T21:15:00+00:00")
        second = client.post(f"/api/change-cases/{case_id}/report").headers["content-disposition"]
        self.assertNotEqual(first, second)
        self.assertIn("run_20261029T211500Z", second)

    def test_report_without_a_ranking_run_uses_the_fallback(self):
        client, case_id = self._case("FnNone")
        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(f"case_{case_id}_run_none.docx", r.headers["content-disposition"])

    def test_cors_exposes_the_header_to_the_browser(self):
        client, case_id = self._case("FnCors")
        r = client.options(f"/api/change-cases/{case_id}/report", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"})
        self.assertIn(r.status_code, (200, 204))
        r = client.post(f"/api/change-cases/{case_id}/report", headers={"Origin": "http://localhost:3000"})
        self.assertIn("content-disposition", r.headers.get("access-control-expose-headers", "").lower())


if __name__ == "__main__":
    unittest.main()
