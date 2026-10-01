"""
B4 -- operator activation of a directly-sold customer organization.
Run with:  python3 -m unittest backend.tests.test_operator_service -v
Uses its own isolated SQLite database (same rebind-and-assert isolation as
test_api.py, so it is safe alongside any other test module in one run).
"""
import io
import os
import shutil
import unittest
from contextlib import redirect_stdout


class TestOperatorActivation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_dir = os.path.join(os.path.dirname(__file__), "_test_env_operator")
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

    def _new_org(self, name):
        """Real signup -> real trial org + trial subscription. Returns org id."""
        type(self)._n += 1
        client = self.client_factory()
        r = client.post("/api/auth/signup", json={
            "email": f"op{self._n}@example.com", "password": "correct-horse-battery-staple",
            "organization_name": name, "display_name": "Op Test"})
        self.assertIn(r.status_code, (200, 201), r.text)
        from backend.app.repositories import organizations_repo
        return organizations_repo.find_organizations_by_exact_name(name)[-1]["id"], client

    def _sub(self, org_id):
        from backend.app.repositories import subscriptions_repo
        return subscriptions_repo.get_subscription(org_id)

    def _audit(self, org_id):
        from backend.app.services import audit_service
        return [a for a in audit_service.list_for_organization(org_id) if a["action"] == "operator_activate_subscription"]

    def test_activates_trial_org_by_id(self):
        from backend.app.services import operator_service
        org_id, _ = self._new_org("Activate By Id Co")
        self.assertEqual(self._sub(org_id)["plan"], "trial")
        r = operator_service.activate_subscription(org_id=org_id, plan="pilot")
        self.assertTrue(r["changed"])
        self.assertEqual((r["previous_plan"], r["previous_status"]), ("trial", "active"))
        self.assertEqual((r["plan"], r["status"]), ("pilot", "active"))
        self.assertEqual((self._sub(org_id)["plan"], self._sub(org_id)["status"]), ("pilot", "active"))

    def test_activates_by_exact_name(self):
        from backend.app.services import operator_service
        org_id, _ = self._new_org("Activate By Name Co")
        r = operator_service.activate_subscription(org_name="Activate By Name Co", plan="enterprise")
        self.assertEqual(r["organization_id"], org_id)
        self.assertEqual(self._sub(org_id)["plan"], "enterprise")

    def test_idempotent_second_run_changes_nothing_and_logs_once(self):
        from backend.app.services import operator_service
        org_id, _ = self._new_org("Idempotent Co")
        first = operator_service.activate_subscription(org_id=org_id, plan="pilot")
        stamp = self._sub(org_id)["updated_at"]
        second = operator_service.activate_subscription(org_id=org_id, plan="pilot")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(self._sub(org_id)["updated_at"], stamp)
        self.assertEqual(len(self._audit(org_id)), 1)

    def test_dry_run_writes_nothing(self):
        from backend.app.services import operator_service
        org_id, _ = self._new_org("Dry Run Co")
        r = operator_service.activate_subscription(org_id=org_id, plan="pilot", apply=False)
        self.assertFalse(r["changed"])
        self.assertEqual(r["would_change_to"], {"plan": "pilot", "status": "active"})
        self.assertEqual(r["plan"], "trial")  # reports current state, not a pretend result
        self.assertEqual(self._sub(org_id)["plan"], "trial")
        self.assertEqual(self._audit(org_id), [])

    def test_refuses_missing_identifier_and_both_identifiers(self):
        from backend.app.services import operator_service
        from backend.app.schemas.errors import ValidationError
        org_id, _ = self._new_org("Both Ids Co")
        with self.assertRaises(ValidationError):
            operator_service.activate_subscription(plan="pilot")
        with self.assertRaises(ValidationError):
            operator_service.activate_subscription(org_id=org_id, org_name="Both Ids Co", plan="pilot")
        self.assertEqual(self._sub(org_id)["plan"], "trial")

    def test_refuses_unknown_org_id_and_name_and_blank_name(self):
        from backend.app.services import operator_service
        from backend.app.schemas.errors import ValidationError
        for kwargs in ({"org_id": 999999}, {"org_name": "No Such Organization"}, {"org_name": "   "}):
            with self.assertRaises(ValidationError, msg=str(kwargs)):
                operator_service.activate_subscription(plan="pilot", **kwargs)

    def test_refuses_ambiguous_name_and_changes_neither(self):
        from backend.app.services import operator_service
        from backend.app.schemas.errors import ValidationError
        a, _ = self._new_org("Twin Name Co")
        b, _ = self._new_org("Twin Name Co")
        self.assertNotEqual(a, b)
        with self.assertRaises(ValidationError) as cm:
            operator_service.activate_subscription(org_name="Twin Name Co", plan="pilot")
        self.assertIn(str(a), str(cm.exception))
        self.assertIn(str(b), str(cm.exception))
        self.assertEqual(self._sub(a)["plan"], "trial")
        self.assertEqual(self._sub(b)["plan"], "trial")
        # the id route still works for exactly the one the operator means
        operator_service.activate_subscription(org_id=b, plan="pilot")
        self.assertEqual(self._sub(a)["plan"], "trial")
        self.assertEqual(self._sub(b)["plan"], "pilot")

    def test_name_match_is_exact_not_partial(self):
        from backend.app.services import operator_service
        from backend.app.schemas.errors import ValidationError
        org_id, _ = self._new_org("Exactly Named Co")
        for wrong in ("Exactly Named", "exactly named co", "Exactly Named Co "):
            with self.assertRaises(ValidationError, msg=wrong):
                operator_service.activate_subscription(org_name=wrong, plan="pilot")
        self.assertEqual(self._sub(org_id)["plan"], "trial")

    def test_refuses_trial_and_unknown_plan(self):
        from backend.app.services import operator_service
        from backend.app.schemas.errors import ValidationError
        org_id, _ = self._new_org("Bad Plan Co")
        for plan in ("trial", "gold", "", None):
            with self.assertRaises(ValidationError, msg=repr(plan)):
                operator_service.activate_subscription(org_id=org_id, plan=plan)
        self.assertEqual(self._sub(org_id)["plan"], "trial")

    def test_only_the_target_org_changes(self):
        from backend.app.services import operator_service
        target, _ = self._new_org("Target Only Co")
        bystander, _ = self._new_org("Bystander Co")
        operator_service.activate_subscription(org_id=target, plan="pilot")
        self.assertEqual(self._sub(bystander)["plan"], "trial")

    def test_reactivates_past_due_org_and_reports_previous_status(self):
        from backend.app.services import operator_service
        from backend.app.repositories import subscriptions_repo
        org_id, _ = self._new_org("Past Due Co")
        subscriptions_repo.set_plan_and_status(org_id, "pilot", "past_due")
        r = operator_service.activate_subscription(org_id=org_id, plan="pilot")
        self.assertTrue(r["changed"])
        self.assertEqual(r["previous_status"], "past_due")
        self.assertEqual(self._sub(org_id)["status"], "active")

    def test_activated_org_can_rank_through_existing_entitlement_rule(self):
        """End to end through the real API: trial is refused, activation
        (and nothing else) is what lets the same org rank."""
        from backend.app.services import operator_service
        org_id, client = self._new_org("Rank After Activation Co")
        spec = {"feature_columns": ["viscosity", "solids_pct"], "target_metric": "adhesion_score",
                "target_value": 80.0, "direction": "maximize"}
        case = client.post("/api/change-cases", json={
            "name": "case", "trigger_type": "regulatory_restriction",
            "restricted_substance": "PFHxA", "qualification_spec": spec}).json()
        csv = "viscosity,solids_pct,adhesion_score\n" + "\n".join(
            f"{450 + i},{60 + i * 0.1},{70 + i * 0.5}" for i in range(15))
        up = client.post(f"/api/change-cases/{case['id']}/dataset",
                         files={"file": ("hist.csv", csv.encode(), "text/csv")})
        self.assertEqual(up.status_code, 201, up.text)
        client.post(f"/api/change-cases/{case['id']}/candidates", json={"name": "A", "features": {"viscosity": 460, "solids_pct": 61}})
        before = client.post(f"/api/change-cases/{case['id']}/rank")
        self.assertEqual(before.status_code, 400)
        operator_service.activate_subscription(org_id=org_id, plan="pilot")
        after = client.post(f"/api/change-cases/{case['id']}/rank")
        self.assertEqual(after.status_code, 200, after.text)

    def test_cli_dry_run_then_apply_then_noop_and_refusal_exit_codes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "activate_subscription_cli",
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "activate_subscription.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        org_id, _ = self._new_org("Cli Co")

        def run(*argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(list(argv))
            return code, buf.getvalue()

        code, out = run("--org-id", str(org_id), "--plan", "pilot")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("Cli Co", out)
        self.assertEqual(self._sub(org_id)["plan"], "trial")
        code, out = run("--org-id", str(org_id), "--plan", "pilot", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("APPLIED", out)
        self.assertIn("plan=pilot status=active", out)
        code, out = run("--org-id", str(org_id), "--plan", "pilot", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("No change", out)
        code, out = run("--plan", "pilot", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        code, out = run("--org-name", "Definitely Not An Org", "--apply")
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)


    def test_review_cli_is_read_only_and_exit_codes_reflect_acceptance(self):
        import importlib.util, json, tempfile
        spec = importlib.util.spec_from_file_location(
            "review_dataset_cli",
            os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "review_dataset.py"))
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        org_id, client = self._new_org("Review Cli Co")
        case_spec = {"feature_columns": ["a", "b"], "target_metric": "y", "target_value": 5.0, "direction": "maximize"}
        case = client.post("/api/change-cases", json={
            "name": "c", "trigger_type": "regulatory_restriction", "restricted_substance": "x",
            "qualification_spec": case_spec}).json()
        rows = "\n".join(f"{i},{i * 2},{i * 3}" for i in range(1, 13))
        with tempfile.TemporaryDirectory() as d:
            alias = os.path.join(d, "alias.csv")
            with open(alias, "w") as f:
                f.write("A Value,b,y\n" + rows + "\n")
            good = os.path.join(d, "good.csv")
            with open(good, "w") as f:
                f.write("a,b,y\n" + rows + "\n")
            opts = os.path.join(d, "opts.json")
            with open(opts, "w") as f:
                json.dump({"column_mapping": {"a": "A Value"}}, f)

            def run(*argv):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    code = cli.main(list(argv))
                return code, buf.getvalue()

            base = ["--org-id", str(org_id), "--case-id", str(case["id"])]
            code, out = run(*base, "--file", good)
            self.assertEqual(code, 0)
            self.assertIn("WOULD BE ACCEPTED", out)
            code, out = run(*base, "--file", alias)
            self.assertEqual(code, 2)
            self.assertIn("MAPPING_NOT_CONFIRMED", out)
            self.assertIn("Proposed (NOT applied", out)
            code, out = run(*base, "--file", alias, "--options", opts)
            self.assertEqual(code, 0)
            self.assertIn("confirmed_mapping", out)
            code, out = run("--org-id", str(org_id), "--case-id", "999999", "--file", good)
            self.assertEqual(code, 1)
            self.assertIn("REFUSED", out)
            code, out = run(*base, "--file", os.path.join(d, "missing.csv"))
            self.assertEqual(code, 1)
        from backend.app.repositories import qualification_dataset_repo
        self.assertEqual(qualification_dataset_repo.list_datasets_for_change_case(org_id, case["id"]), [])  # read-only


if __name__ == "__main__":
    unittest.main()
