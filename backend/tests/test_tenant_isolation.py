"""
Level 1 -- one organization can never read or change another's resources by guessing ids.

Organization A builds a complete diagnostic (case, dataset, candidate, ranking, recommended
experiment, outcome). Organization B then tries every route with A's identifiers. Each attempt must
be refused (404 -- never 403, so existence is not revealed) AND leave A's data exactly as it was.

Run:  python3 -m unittest backend.tests.test_tenant_isolation -v
"""
import json
import os
import tempfile
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import SPEC, IsolatedApiTestCase  # noqa: E402

HEAD = ["crosslinker_ratio", "cure_temp_c", "salt_spray_hours"]


class TestTenantIsolation(IsolatedApiTestCase):
    dir_name = "_test_env_isolation"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._built = False

    def build(self):
        """Organization A's complete diagnostic, built once per class."""
        cls = type(self)
        if cls._built:
            return
        cls.org_a, cls.a = self.org_and_client("IsoA")
        cls.org_b, cls.b = self.org_and_client("IsoB")
        self.paid(cls.org_a)
        self.paid(cls.org_b)
        cls.case_id = self.make_case(cls.a, SPEC)
        r = self.upload(cls.a, cls.case_id, "h.csv", F.csv_bytes(HEAD, F.base_rows(14)))
        assert r.status_code == 201, r.text
        cls.dataset_id = r.json()["dataset_id"]
        cand = cls.a.post(f"/api/change-cases/{cls.case_id}/candidates",
                          json={"name": "Alpha", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}})
        assert cand.status_code == 201, cand.text
        cls.cand_id = cand.json()["id"]
        assert cls.a.post(f"/api/change-cases/{cls.case_id}/rank").status_code == 200
        from backend.app.repositories import candidate_repo
        cls.exp_id = candidate_repo.list_recommended_experiments_for_candidate(cls.org_a, cls.cand_id)[0]["id"]
        cls._built = True

    def snapshot(self):
        """Everything of A's that B could conceivably damage."""
        listing = self.a.get(f"/api/change-cases/{self.case_id}/candidates").json()
        case = self.a.get(f"/api/change-cases/{self.case_id}").json()
        from backend.app.repositories import candidate_repo
        return json.dumps({
            "case": case, "candidates": listing, "datasets": [d["id"] for d in self.datasets(self.org_a, self.case_id)],
            "experiments": len(self.experiments(self.org_a, self.dataset_id)),
            "outcomes": candidate_repo.count_outcomes_for_candidate(self.org_a, self.cand_id),
        }, sort_keys=True, default=str)

    def setUp(self):
        self.build()
        self.before = self.snapshot()

    def tearDown(self):
        self.assertEqual(self.snapshot(), self.before, "organization A's data changed")

    # ------------------------------------------------------------------ reads
    def test_b_cannot_read_a_diagnostic(self):
        self.assertEqual(self.b.get(f"/api/change-cases/{self.case_id}").status_code, 404)
        self.assertEqual(self.b.get(f"/api/change-cases/{self.case_id}/candidates").status_code, 404)
        self.assertEqual(self.b.get(f"/api/change-cases/{self.case_id}/evidence").status_code, 404)
        self.assertEqual(self.b.get(f"/api/change-cases/{self.case_id}/sufficiency").status_code, 404)

    def test_b_listing_never_shows_a_diagnostic(self):
        # (B may have created cases of its own in other tests; none of them may be A's)
        b_ids = [c["id"] for c in self.b.get("/api/change-cases").json()]
        self.assertNotIn(self.case_id, b_ids)
        self.assertTrue(all(c["organization_id"] == self.org_b for c in self.b.get("/api/change-cases").json()))
        self.assertTrue(all(c["organization_id"] == self.org_a for c in self.a.get("/api/change-cases").json()))
        self.assertIn(self.case_id, [c["id"] for c in self.a.get("/api/change-cases").json()])

    def test_response_for_someone_elses_id_is_the_same_as_for_a_missing_id(self):
        theirs = self.b.get(f"/api/change-cases/{self.case_id}")
        missing = self.b.get("/api/change-cases/987654")
        self.assertEqual((theirs.status_code, theirs.json()), (missing.status_code, missing.json()))

    # ------------------------------------------------------------------ writes
    def test_b_cannot_change_a_diagnostics_status(self):
        r = self.b.patch(f"/api/change-cases/{self.case_id}/status", json={"status": "completed"})
        self.assertEqual(r.status_code, 404)

    def test_b_cannot_add_a_candidate_to_a_diagnostic(self):
        r = self.b.post(f"/api/change-cases/{self.case_id}/candidates",
                        json={"name": "Intruder", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}})
        self.assertEqual(r.status_code, 404)

    def test_b_cannot_upload_evidence_to_a_diagnostic(self):
        csv = F.csv_bytes(HEAD, F.base_rows(12))
        for route in ("dataset", "dataset/preview", "evidence/extract"):
            r = self.b.post(f"/api/change-cases/{self.case_id}/{route}", files={"file": ("x.csv", csv, "text/csv")})
            self.assertEqual(r.status_code, 404, route)

    def test_b_cannot_rank_or_report_on_a_diagnostic(self):
        self.assertEqual(self.b.post(f"/api/change-cases/{self.case_id}/rank").status_code, 404)
        self.assertEqual(self.b.post(f"/api/change-cases/{self.case_id}/report").status_code, 404)

    def test_b_cannot_record_an_outcome_for_a_candidate_of_a(self):
        body = {"actual_result": "forged", "passed_spec": True}
        self.assertEqual(self.b.post(f"/api/change-cases/{self.case_id}/candidates/{self.cand_id}/outcome", json=body).status_code, 404)

    def test_b_cannot_record_an_outcome_through_its_own_case_with_a_candidate_of_a(self):
        b_case = self.make_case(self.b, SPEC)
        body = {"actual_result": "forged", "passed_spec": True}
        r = self.b.post(f"/api/change-cases/{b_case}/candidates/{self.cand_id}/outcome", json=body)
        self.assertEqual(r.status_code, 404)

    def test_b_cannot_attach_a_recommended_experiment_of_a_to_its_own_candidate(self):
        from backend.app.repositories import candidate_repo
        self.paid(self.org_b)
        b_case = self.make_case(self.b, SPEC)
        self.assertEqual(self.upload(self.b, b_case, "h.csv", F.csv_bytes(HEAD, F.base_rows(14))).status_code, 201)
        b_cand = self.b.post(f"/api/change-cases/{b_case}/candidates",
                             json={"name": "Beta", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}}).json()["id"]
        r = self.b.post(f"/api/change-cases/{b_case}/candidates/{b_cand}/outcome", json={
            "recommended_experiment_id": self.exp_id, "actual_result": "x", "passed_spec": True})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["field"], "recommended_experiment_id")
        self.assertEqual(candidate_repo.count_outcomes_for_candidate(self.org_b, b_cand), 0)

    def test_an_experiment_of_a_different_candidate_in_the_same_organization_is_refused(self):
        from backend.app.repositories import candidate_repo
        other = self.a.post(f"/api/change-cases/{self.case_id}/candidates",
                            json={"name": "Second", "features": {"crosslinker_ratio": 0.12, "cure_temp_c": 155}}).json()["id"]
        try:
            r = self.a.post(f"/api/change-cases/{self.case_id}/candidates/{other}/outcome", json={
                "recommended_experiment_id": self.exp_id, "actual_result": "x", "passed_spec": True})
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(candidate_repo.count_outcomes_for_candidate(self.org_a, other), 0)
        finally:
            from sqlalchemy import text
            from backend.app.config import database
            with database.engine.begin() as c:
                c.execute(text("DELETE FROM candidate_substitutes WHERE id = :id"), {"id": other})

    def test_a_nonexistent_experiment_id_is_a_clean_400_not_a_500(self):
        r = self.a.post(f"/api/change-cases/{self.case_id}/candidates/{self.cand_id}/outcome", json={
            "recommended_experiment_id": 987654, "actual_result": "x", "passed_spec": True})
        self.assertEqual(r.status_code, 400, r.text)

    # ------------------------------------------------------------------ unauthenticated
    def test_every_route_requires_a_session(self):
        anon = self.client_factory()
        cid = self.case_id
        calls = [
            ("get", "/api/change-cases", None), ("get", f"/api/change-cases/{cid}", None),
            ("patch", f"/api/change-cases/{cid}/status", {"status": "active"}),
            ("get", f"/api/change-cases/{cid}/candidates", None),
            ("post", f"/api/change-cases/{cid}/candidates", {"name": "x", "features": {}}),
            ("post", f"/api/change-cases/{cid}/rank", None), ("post", f"/api/change-cases/{cid}/report", None),
            ("get", f"/api/change-cases/{cid}/evidence", None), ("get", f"/api/change-cases/{cid}/sufficiency", None),
            ("post", f"/api/change-cases/{cid}/candidates/{self.cand_id}/outcome", {"actual_result": "x", "passed_spec": True}),
            ("get", "/api/team/members", None), ("get", "/api/team/invitations", None),
            ("get", "/api/dashboard/summary", None), ("get", "/api/audit-log", None),
        ]
        for method, url, body in calls:
            r = getattr(anon, method)(url, **({"json": body} if body is not None else {}))
            self.assertEqual(r.status_code, 401, (method, url, r.status_code))

    # ------------------------------------------------------------------ legacy project / report records
    def test_b_cannot_see_or_download_a_legacy_project_report_of_a(self):
        from backend.app.repositories import reports_repo
        fd, path = tempfile.mkstemp(suffix=".docx")
        os.write(fd, b"PK-not-a-real-docx")
        os.close(fd)
        proj = self.a.post("/api/projects", json={
            "name": "legacy", "target_metric": "y", "direction": "maximize", "feature_columns": ["a", "b"]})
        self.assertEqual(proj.status_code, 201, proj.text)
        project_id = proj.json()["id"]
        try:
            report_id = reports_repo.create_report(self.org_a, project_id, 1, path, 0)
            self.assertEqual(self.a.get(f"/api/reports/{report_id}/download").status_code, 200)
            self.assertEqual(self.b.get(f"/api/reports/{report_id}/download").status_code, 404)
            self.assertEqual(self.b.get(f"/api/projects/{project_id}").status_code, 404)
            self.assertEqual(self.b.get(f"/api/projects/{project_id}/reports").status_code, 404)
            self.assertEqual(self.b.post(f"/api/projects/{project_id}/report").status_code, 404)
            self.assertEqual(self.b.get(f"/api/projects/{project_id}/experiments").status_code, 404)
            self.assertNotIn(project_id, [p["id"] for p in self.b.get("/api/projects").json()])
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()