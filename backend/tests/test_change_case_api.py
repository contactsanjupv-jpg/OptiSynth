"""
End-to-end tests for the forced-substitution qualification diagnostic API.
Same setup pattern as test_api.py -- run with:

    pip install -r backend/requirements.txt
    python3 -m unittest backend.tests.test_change_case_api -v

Genuinely runtime-tested in this project's own sandbox during Phase 2
implementation (real FastAPI TestClient, real SQLite database, real
Alembic migrations applied) -- not a syntax-only port.
"""
import os
import shutil
import unittest


class TestChangeCaseApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = os.path.join(os.path.dirname(__file__), "_test_env_change_case")
        os.makedirs(cls.test_dir, exist_ok=True)
        os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
        os.environ["PASSWORD_PEPPER"] = "test-password-pepper-not-for-production"
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.test_dir}/test.db"
        if os.path.exists(f"{cls.test_dir}/test.db"):
            os.remove(f"{cls.test_dir}/test.db")

        from fastapi.testclient import TestClient
        from backend.app.main import app
        from alembic.config import Config
        from alembic import command

        # init_db() (used by test_api.py) only runs the ORIGINAL
        # schema.sql and does not know about the Phase 2 tables, which
        # exist only as Alembic migrations -- see
        # PROJECT_ARCHITECTURE.md rule 6. Real Alembic migrations are the
        # only thing that creates the full current schema, so that's
        # what this test suite's setup must use.
        backend_dir = os.path.join(os.path.dirname(__file__), "..")
        alembic_cfg = Config(os.path.join(backend_dir, "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
        command.upgrade(alembic_cfg, "head")

        cls.client_factory = staticmethod(lambda: TestClient(app))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        self.client = self.client_factory()

    def _spec(self):
        return {
            "feature_columns": ["viscosity", "solids_pct"],
            "target_metric": "adhesion_score",
            "target_value": 80.0,
            "direction": "maximize",
        }

    def _signup(self, client, email, org_name):
        return client.post("/api/auth/signup", json={
            "email": email, "password": "correct-horse-battery-staple",
            "organization_name": org_name, "display_name": "Test Chemist",
        })

    def _create_change_case(self, client, name="PFHxA replacement"):
        return client.post("/api/change-cases", json={
            "name": name, "trigger_type": "regulatory_restriction",
            "restricted_substance": "PFHxA", "qualification_spec": self._spec(),
        })

    def _upgrade_org_to_paid(self, org_name: str):
        # Uses the app's own db_transaction() -- guaranteed to hit
        # whatever database the app itself is actually using -- rather
        # than a raw sqlite3.connect() to an assumed file path. The
        # latter broke when this test file ran in the same process as
        # test_api.py: `settings` is a singleton frozen at first import,
        # so a second test file's setUpClass re-assigning
        # os.environ["DATABASE_URL"] doesn't retroactively change which
        # database the already-imported app is connected to, while this
        # file's own self.db_path still pointed at its own (different,
        # unused-by-the-app) intended file. Real bug, found by running
        # the full combined suite, not just this file alone.
        from sqlalchemy import text
        from backend.app.config.database import db_transaction
        with db_transaction() as conn:
            conn.execute(
                text("UPDATE subscriptions SET plan='pilot' WHERE organization_id="
                     "(SELECT id FROM organizations WHERE name=:name)"),
                {"name": org_name},
            )

    def _csv(self, n=15):
        header = "viscosity,solids_pct,adhesion_score\n"
        rows = "\n".join(f"{450+i},{60+i*0.1},{70+i*0.5}" for i in range(n))
        return header + rows

    # -----------------------------------------------------------------
    # Core workflow
    # -----------------------------------------------------------------

    def test_full_diagnostic_workflow(self):
        self._signup(self.client, "workflow@coatings.com", "Workflow Coatings")
        r = self._create_change_case(self.client)
        self.assertEqual(r.status_code, 201)
        case_id = r.json()["id"]
        self.assertEqual(r.json()["status"], "draft")

        r = self.client.post(
            f"/api/change-cases/{case_id}/dataset",
            files={"file": ("history.csv", self._csv(), "text/csv")},
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["rows_ingested"], 15)

        r = self.client.post(f"/api/change-cases/{case_id}/candidates",
                              json={"name": "Resin A", "features": {"viscosity": 460, "solids_pct": 61}})
        self.assertEqual(r.status_code, 201)
        candidate_id = r.json()["id"]

        self._upgrade_org_to_paid("Workflow Coatings")

        r = self.client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 200)
        results = r.json()
        self.assertEqual(len(results), 1)
        self.assertIn("predicted_probability", results[0])
        self.assertIn("recommended_experiment", results[0])

        r = self.client.post(
            f"/api/change-cases/{case_id}/candidates/{candidate_id}/outcome",
            json={"actual_result": "Passed adhesion at 84 vs target 80", "passed_spec": True},
        )
        self.assertEqual(r.status_code, 201)

    # -----------------------------------------------------------------
    # Entitlement gating
    # -----------------------------------------------------------------

    def test_ranking_blocked_on_trial_plan(self):
        client = self.client_factory()
        self._signup(client, "trial@coatings.com", "Trial Coatings")
        r = self._create_change_case(client)
        case_id = r.json()["id"]
        client.post(f"/api/change-cases/{case_id}/dataset",
                    files={"file": ("h.csv", self._csv(), "text/csv")})
        client.post(f"/api/change-cases/{case_id}/candidates",
                    json={"name": "A", "features": {"viscosity": 460, "solids_pct": 61}})

        r = client.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Trial", r.json()["error"])

    # -----------------------------------------------------------------
    # Tenant isolation -- every new endpoint, both trial and paid requester
    # -----------------------------------------------------------------

    def test_tenant_isolation_get_change_case(self):
        client_a = self.client_factory()
        self._signup(client_a, "iso-a1@coatings.com", "Iso Org A1")
        case_id = self._create_change_case(client_a).json()["id"]

        client_b = self.client_factory()
        self._signup(client_b, "iso-b1@coatings.com", "Iso Org B1")
        r = client_b.get(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 404)

    def test_tenant_isolation_list_candidates(self):
        client_a = self.client_factory()
        self._signup(client_a, "iso-a2@coatings.com", "Iso Org A2")
        case_id = self._create_change_case(client_a).json()["id"]

        client_b = self.client_factory()
        self._signup(client_b, "iso-b2@coatings.com", "Iso Org B2")
        r = client_b.get(f"/api/change-cases/{case_id}/candidates")
        self.assertEqual(r.status_code, 404)

    def test_tenant_isolation_ranking_uniform_404_regardless_of_requester_plan(self):
        """The specific ordering bug found and fixed during Phase 2
        implementation: ranking must return 404 for a change case that
        isn't the requester's, whether the requester is on a trial plan
        or a paid one -- ownership must be checked before entitlement, so
        a trial-plan requester probing another org's change_case_id gets
        the same response as a paid one would."""
        client_a = self.client_factory()
        self._signup(client_a, "iso-a3@coatings.com", "Iso Org A3")
        case_id = self._create_change_case(client_a).json()["id"]

        # Trial requester
        client_b = self.client_factory()
        self._signup(client_b, "iso-b3@coatings.com", "Iso Org B3")
        r = client_b.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 404, "trial requester must get 404, not a 400 about their own plan")

        # Paid requester
        client_c = self.client_factory()
        self._signup(client_c, "iso-c3@coatings.com", "Iso Org C3")
        self._upgrade_org_to_paid("Iso Org C3")
        r = client_c.post(f"/api/change-cases/{case_id}/rank")
        self.assertEqual(r.status_code, 404)

    def test_tenant_isolation_record_outcome(self):
        client_a = self.client_factory()
        self._signup(client_a, "iso-a4@coatings.com", "Iso Org A4")
        case_id = self._create_change_case(client_a).json()["id"]
        client_a.post(f"/api/change-cases/{case_id}/dataset",
                       files={"file": ("h.csv", self._csv(), "text/csv")})
        cand = client_a.post(f"/api/change-cases/{case_id}/candidates",
                              json={"name": "A", "features": {"viscosity": 460, "solids_pct": 61}}).json()

        client_b = self.client_factory()
        self._signup(client_b, "iso-b4@coatings.com", "Iso Org B4")
        r = client_b.post(f"/api/change-cases/{case_id}/candidates/{cand['id']}/outcome",
                           json={"actual_result": "fabricated", "passed_spec": True})
        self.assertEqual(r.status_code, 404)

    # -----------------------------------------------------------------
    # Append-only outcomes, enforced at the route level
    # -----------------------------------------------------------------

    def test_duplicate_outcome_rejected_at_route_level(self):
        client = self.client_factory()
        self._signup(client, "append@coatings.com", "Append Coatings")
        case_id = self._create_change_case(client).json()["id"]
        client.post(f"/api/change-cases/{case_id}/dataset",
                    files={"file": ("h.csv", self._csv(), "text/csv")})
        cand = client.post(f"/api/change-cases/{case_id}/candidates",
                            json={"name": "A", "features": {"viscosity": 460, "solids_pct": 61}}).json()

        r1 = client.post(f"/api/change-cases/{case_id}/candidates/{cand['id']}/outcome",
                          json={"actual_result": "first", "passed_spec": True})
        self.assertEqual(r1.status_code, 201)

        r2 = client.post(f"/api/change-cases/{case_id}/candidates/{cand['id']}/outcome",
                          json={"actual_result": "second attempt", "passed_spec": False})
        self.assertEqual(r2.status_code, 400)

    # -----------------------------------------------------------------
    # Provenance: every prediction is tied to the dataset actually used
    # -----------------------------------------------------------------

    def test_prediction_provenance_tracks_dataset_version(self):
        client = self.client_factory()
        self._signup(client, "provenance@coatings.com", "Provenance Coatings")
        case_id = self._create_change_case(client).json()["id"]

        r = client.post(f"/api/change-cases/{case_id}/dataset",
                         files={"file": ("v1.csv", self._csv(15), "text/csv")})
        first_dataset_id = r.json()["dataset_id"]

        client.post(f"/api/change-cases/{case_id}/candidates",
                    json={"name": "A", "features": {"viscosity": 460, "solids_pct": 61}})
        self._upgrade_org_to_paid("Provenance Coatings")
        client.post(f"/api/change-cases/{case_id}/rank")

        candidates = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.assertEqual(candidates[0]["latest_prediction"]["dataset_version_id"], first_dataset_id)

        # Re-upload a NEW dataset version, re-rank, confirm the prediction
        # now points at the NEW dataset, not the stale first one.
        r2 = client.post(f"/api/change-cases/{case_id}/dataset",
                          files={"file": ("v2.csv", self._csv(20), "text/csv")})
        second_dataset_id = r2.json()["dataset_id"]
        self.assertNotEqual(first_dataset_id, second_dataset_id)

        client.post(f"/api/change-cases/{case_id}/rank")
        candidates = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.assertEqual(candidates[0]["latest_prediction"]["dataset_version_id"], second_dataset_id)

    # -----------------------------------------------------------------
    # Scope limits (3-5 candidates, no candidate discovery)
    # -----------------------------------------------------------------

    def test_candidate_limit_enforced(self):
        client = self.client_factory()
        self._signup(client, "limit@coatings.com", "Limit Coatings")
        case_id = self._create_change_case(client).json()["id"]

        for i in range(5):
            r = client.post(f"/api/change-cases/{case_id}/candidates",
                             json={"name": f"Candidate {i}", "features": {"viscosity": 460, "solids_pct": 61}})
            self.assertEqual(r.status_code, 201)

        r = client.post(f"/api/change-cases/{case_id}/candidates",
                         json={"name": "One too many", "features": {"viscosity": 460, "solids_pct": 61}})
        self.assertEqual(r.status_code, 400)

    # -----------------------------------------------------------------
    # Report generation -- the commercial deliverable
    # -----------------------------------------------------------------

    def test_report_generation_produces_real_docx(self):
        client = self.client_factory()
        self._signup(client, "report@coatings.com", "Report Coatings")
        case_id = self._create_change_case(client).json()["id"]
        client.post(f"/api/change-cases/{case_id}/dataset",
                    files={"file": ("h.csv", self._csv(), "text/csv")})
        client.post(f"/api/change-cases/{case_id}/candidates",
                    json={"name": "Resin A", "features": {"viscosity": 460, "solids_pct": 61}})
        self._upgrade_org_to_paid("Report Coatings")
        client.post(f"/api/change-cases/{case_id}/rank")

        r = client.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"],
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertGreater(len(r.content), 0)

    def test_tenant_isolation_report_generation(self):
        client_a = self.client_factory()
        self._signup(client_a, "iso-a5@coatings.com", "Iso Org A5")
        case_id = self._create_change_case(client_a).json()["id"]

        client_b = self.client_factory()
        self._signup(client_b, "iso-b5@coatings.com", "Iso Org B5")
        r = client_b.post(f"/api/change-cases/{case_id}/report")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
