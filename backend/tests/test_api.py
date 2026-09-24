"""
End-to-end backend tests using FastAPI's TestClient (built on Starlette/
httpx) + Python's stdlib unittest. Run on your machine with:

    pip install -r backend/requirements.txt
    python3 -m unittest backend.tests.test_api -v

NOT RUNTIME-TESTED IN THE SANDBOX: fastapi/httpx are not installed here,
so this file has been syntax-validated (python3 -m py_compile) but NOT
executed. It is a direct port of a test suite that WAS executed and
passed (11/11) against the equivalent Flask backend earlier in this
project's development -- see README "What changed between the Flask and
FastAPI versions" for the mapping between the two. Please run this for
real on your machine before deploying and report back if anything fails.
"""
import io
import os
import shutil
import unittest


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = os.path.join(os.path.dirname(__file__), "_test_env")
        os.makedirs(cls.test_dir, exist_ok=True)
        os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
        os.environ["PASSWORD_PEPPER"] = "test-password-pepper-not-for-production"
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.test_dir}/test.db"
        if os.path.exists(f"{cls.test_dir}/test.db"):
            os.remove(f"{cls.test_dir}/test.db")

        from fastapi.testclient import TestClient
        from backend.app.config.database import init_db
        from backend.app.main import app
        # Explicit call rather than relying on FastAPI's lifespan startup
        # event -- plain TestClient(app) (without `with`) does not reliably
        # trigger lifespan handlers, which left the database with no tables
        # created and caused "no such table: users" errors further down.
        init_db()
        # staticmethod() is required here -- without it, Python treats a
        # plain lambda stored as a class attribute like a bound method and
        # auto-passes `self` as an argument when called via
        # self.client_factory(), causing "takes 0 positional arguments but
        # 1 was given".
        cls.client_factory = staticmethod(lambda: TestClient(app))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self):
        self.client = self.client_factory()

    def _signup(self, client, email, org_name):
        return client.post("/api/auth/signup", json={
            "email": email, "password": "correct-horse-battery-staple",
            "organization_name": org_name, "display_name": "Test User",
        })

    def _create_project(self, client):
        return client.post("/api/projects", json={
            "name": "Adhesive Optimization",
            "objective": "Maximize bond strength",
            "target_metric": "bond_strength_MPa",
            "direction": "maximize",
            "target_value": 20.0,
            "feature_columns": ["resin_frac", "hardener_frac", "filler_frac",
                                 "solvent_frac", "cure_temp_C", "cure_time_min"],
            "constraints": [{"column": "viscosity_Pa_s", "op": "<=", "value": 5.0}],
        })

    def _sample_csv(self):
        import random
        header = ("resin_frac,hardener_frac,filler_frac,solvent_frac,cure_temp_C,"
                   "cure_time_min,bond_strength_MPa,viscosity_Pa_s\n")
        rng = random.Random(0)
        rows = []
        for _ in range(20):
            temp = rng.uniform(80, 130)
            time_ = rng.uniform(20, 90)
            strength = 15 + rng.uniform(0, 8)
            visc = 2 + rng.uniform(0, 2)
            rows.append(f"0.4,0.25,0.2,0.15,{temp:.1f},{time_:.1f},{strength:.2f},{visc:.2f}")
        return (header + "\n".join(rows)).encode("utf-8")

    def test_health_check(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)

    def test_signup_then_me(self):
        r = self._signup(self.client, "owner@acme-test.com", "Acme Coatings")
        self.assertEqual(r.status_code, 201, r.text)
        r2 = self.client.get("/api/auth/me")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["user"]["email"], "owner@acme-test.com")

    def test_signup_rejects_short_password(self):
        r = self.client.post("/api/auth/signup", json={
            "email": "shortpw@acme-test.com", "password": "short", "organization_name": "Acme",
        })
        self.assertEqual(r.status_code, 422)  # Pydantic shape validation -> FastAPI's 422

    def test_login_wrong_password_fails(self):
        self._signup(self.client, "loginfail@acme-test.com", "Acme")
        self.client.post("/api/auth/logout")
        r = self.client.post("/api/auth/login", json={
            "email": "loginfail@acme-test.com", "password": "totally-wrong-password",
        })
        self.assertEqual(r.status_code, 400)

    def test_unauthenticated_request_rejected(self):
        fresh_client = self.client_factory()
        r = fresh_client.get("/api/projects")
        self.assertEqual(r.status_code, 401)

    def test_logout_then_protected_route_rejected(self):
        self._signup(self.client, "logout-test@acme-test.com", "Acme")
        self.client.post("/api/auth/logout")
        r = self.client.get("/api/projects")
        self.assertEqual(r.status_code, 401)

    def test_tenant_isolation_cannot_see_other_orgs_project(self):
        client_a = self.client_factory()
        self._signup(client_a, "org-a@test.com", "Org A")
        r = self._create_project(client_a)
        project_id = r.json()["id"]

        client_b = self.client_factory()
        self._signup(client_b, "org-b@test.com", "Org B")
        r2 = client_b.get(f"/api/projects/{project_id}")
        self.assertEqual(r2.status_code, 404, "Org B must not be able to see Org A's project")

        r3 = client_b.get("/api/projects")
        ids = [p["id"] for p in r3.json()]
        self.assertNotIn(project_id, ids)

    def test_full_workflow(self):
        self._signup(self.client, "workflow@acme-test.com", "Workflow Acme")
        r = self._create_project(self.client)
        self.assertEqual(r.status_code, 201)
        project_id = r.json()["id"]

        r2 = self.client.post(
            f"/api/projects/{project_id}/dataset",
            files={"file": ("history.csv", io.BytesIO(self._sample_csv()), "text/csv")},
        )
        self.assertEqual(r2.status_code, 201, r2.text)
        self.assertGreater(r2.json()["rows_ingested"], 0)

        r3 = self.client.get(f"/api/projects/{project_id}/experiments")
        self.assertEqual(r3.status_code, 200)
        self.assertGreater(len(r3.json()), 0)

        r4 = self.client.post(f"/api/projects/{project_id}/recommend", json={"n_recommendations": 3})
        self.assertEqual(r4.status_code, 200, r4.text)
        self.assertEqual(len(r4.json()["recommendations"]), 3)

        r5 = self.client.get(f"/api/projects/{project_id}/backtest")
        self.assertEqual(r5.status_code, 200)

        r6 = self.client.get(f"/api/projects/{project_id}/model-metrics")
        self.assertEqual(r6.status_code, 200)

        r7 = self.client.post(f"/api/projects/{project_id}/report")
        self.assertEqual(r7.status_code, 201, r7.text)
        report_id = r7.json()["report_id"]

        r8 = self.client.get(f"/api/reports/{report_id}/download")
        self.assertEqual(r8.status_code, 200)
        self.assertGreater(len(r8.content), 0)

        r9 = self.client.get("/api/dashboard/summary")
        self.assertEqual(r9.status_code, 200)
        self.assertGreaterEqual(r9.json()["total_experiments"], 1)

        r10 = self.client.get("/api/audit-log")
        self.assertEqual(r10.status_code, 200)
        self.assertGreater(len(r10.json()), 0)

    def test_dataset_rejects_non_csv(self):
        self._signup(self.client, "badfile@acme-test.com", "Acme")
        r = self._create_project(self.client)
        project_id = r.json()["id"]
        r2 = self.client.post(
            f"/api/projects/{project_id}/dataset",
            files={"file": ("history.txt", io.BytesIO(b"not a csv"), "text/plain")},
        )
        self.assertEqual(r2.status_code, 400)

    def test_dataset_rejects_missing_columns(self):
        self._signup(self.client, "badcols@acme-test.com", "Acme")
        r = self._create_project(self.client)
        project_id = r.json()["id"]
        r2 = self.client.post(
            f"/api/projects/{project_id}/dataset",
            files={"file": ("history.csv", io.BytesIO(b"only_one_column\n1\n2\n"), "text/csv")},
        )
        self.assertEqual(r2.status_code, 400)

    def test_error_response_never_leaks_stack_trace(self):
        self._signup(self.client, "safeerr@acme-test.com", "Acme")
        r = self.client.get("/api/projects/999999")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("Traceback", r.text)
        self.assertNotIn(".py", r.text)


if __name__ == "__main__":
    unittest.main()
