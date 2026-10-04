"""
Shared base for API tests that need their own isolated SQLite database.
Same rebind-and-assert isolation as test_api.py / test_change_case_api.py, so
every module using it is safe alongside any other module in one combined run.
"""
import io
import json
import os
import shutil
import unittest

MIME = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}

SPEC = {"feature_columns": ["crosslinker_ratio", "cure_temp_c"], "target_metric": "salt_spray_hours",
        "target_value": 300.0, "direction": "maximize"}
SPEC_H = {**SPEC, "units": {"salt_spray_hours": "h"}}
SPEC_U = {**SPEC, "units": {"cure_temp_c": "degC", "salt_spray_hours": "h"}}

CONFIRM_ALIASES = {"column_mapping": {
    "crosslinker_ratio": "Crosslinker Ratio", "cure_temp_c": "Cure Temp (C)", "salt_spray_hours": "Salt Spray (h)"}}


class IsolatedApiTestCase(unittest.TestCase):
    dir_name = "_test_env_iso"

    @classmethod
    def setUpClass(cls):
        cls.test_dir = os.path.join(os.path.dirname(__file__), cls.dir_name)
        os.makedirs(cls.test_dir, exist_ok=True)
        os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
        os.environ["PASSWORD_PEPPER"] = "test-password-pepper-not-for-production"
        os.environ["DATABASE_URL"] = f"sqlite:///{cls.test_dir}/test.db"
        if os.path.exists(f"{cls.test_dir}/test.db"):
            os.remove(f"{cls.test_dir}/test.db")
        from backend.app.config.settings import settings
        from backend.app.config import database
        settings.DATABASE_URL = os.environ["DATABASE_URL"]
        database.engine = database._make_engine()
        assert str(database.engine.url).endswith(f"{cls.test_dir}/test.db"), (
            f"Test isolation failure: engine bound to {database.engine.url!r}"
        )
        from alembic.config import Config
        from alembic import command
        backend_dir = os.path.join(os.path.dirname(__file__), "..")
        cfg = Config(os.path.join(backend_dir, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
        command.upgrade(cfg, "head")
        from fastapi.testclient import TestClient
        from backend.app.main import app
        cls.client_factory = staticmethod(lambda: TestClient(app))
        cls._n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def org_and_client(self, label):
        type(self)._n += 1
        client = self.client_factory()
        name = f"{label} {self._n} Co"
        r = client.post("/api/auth/signup", json={
            "email": f"{self.dir_name}{self._n}@example.com", "password": "correct-horse-battery-staple",
            "organization_name": name, "display_name": "Test User"})
        assert r.status_code in (200, 201), r.text
        from backend.app.repositories import organizations_repo
        return organizations_repo.find_organizations_by_exact_name(name)[0]["id"], client

    def make_case(self, client, spec):
        r = client.post("/api/change-cases", json={
            "name": "case", "trigger_type": "regulatory_restriction",
            "restricted_substance": "PFHxA", "qualification_spec": spec})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def setup_case(self, label, spec=SPEC):
        org_id, client = self.org_and_client(label)
        return org_id, client, self.make_case(client, spec)

    def post_file(self, client, case_id, filename, content, options=None, route="dataset"):
        ext = os.path.splitext(filename)[1].lower()
        data = {"options": json.dumps(options)} if options is not None else None
        return client.post(f"/api/change-cases/{case_id}/{route}",
                           files={"file": (filename, content, MIME.get(ext, "application/octet-stream"))}, data=data)

    def upload(self, client, case_id, filename, content, options=None):
        return self.post_file(client, case_id, filename, content, options, "dataset")

    def preview(self, client, case_id, filename, content, options=None):
        return self.post_file(client, case_id, filename, content, options, "dataset/preview")

    def extract(self, client, case_id, filename, content):
        return self.post_file(client, case_id, filename, content, None, "evidence/extract")

    def datasets(self, org_id, case_id):
        from backend.app.repositories import qualification_dataset_repo
        return qualification_dataset_repo.list_datasets_for_change_case(org_id, case_id)

    def experiments(self, org_id, dataset_id):
        from backend.app.repositories import qualification_dataset_repo
        return qualification_dataset_repo.list_experiments_for_dataset(org_id, dataset_id)

    def paid(self, org_id):
        from backend.app.services import operator_service
        operator_service.activate_subscription(org_id=org_id, plan="pilot")

    def assert_rejected_nothing_stored(self, org_id, case_id, resp, code):
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn(code, resp.json()["error"])
        self.assertEqual(self.datasets(org_id, case_id), [], "a rejected upload must store nothing")

    def docx_text(self, content):
        from docx import Document
        doc = Document(io.BytesIO(content))
        parts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for row in t.rows:
                parts.extend(c.text for c in row.cells)
        return "\n".join(parts)
