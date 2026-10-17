"""
Level 1 -- SQLite foreign keys are actually enforced (the pragma is per-connection and defaults to
OFF, so before this every REFERENCES / ON DELETE clause in the schema was documentation only).

Run:  python3 -m unittest backend.tests.test_foreign_key_enforcement -v
"""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import SPEC, IsolatedApiTestCase  # noqa: E402

HEAD = ["crosslinker_ratio", "cure_temp_c", "salt_spray_hours"]


class TestForeignKeys(IsolatedApiTestCase):
    dir_name = "_test_env_fk"

    def conn(self):
        from backend.app.config import database
        return database.engine

    def count(self, sql, **params):
        with self.conn().connect() as c:
            return c.execute(text(sql), params).scalar()

    def full_case(self, label):
        """org, client, case with a dataset, one candidate, a ranking and a recommended experiment."""
        org_id, client, case_id = self.setup_case(label, SPEC)
        self.paid(org_id)
        self.assertEqual(self.upload(client, case_id, "h.csv", F.csv_bytes(HEAD, F.base_rows(14))).status_code, 201)
        cand = client.post(f"/api/change-cases/{case_id}/candidates",
                           json={"name": "A", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}}).json()
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        return org_id, client, case_id, cand["id"]

    # ------------------------------------------------------------------ the pragma itself
    def test_every_connection_has_foreign_keys_on(self):
        for _ in range(3):
            with self.conn().connect() as c:
                self.assertEqual(c.execute(text("PRAGMA foreign_keys")).scalar(), 1)
        with self.conn().begin() as c:
            self.assertEqual(c.execute(text("PRAGMA foreign_keys")).scalar(), 1)

    # ------------------------------------------------------------------ invalid references are rejected
    def test_candidate_for_a_nonexistent_change_case_is_rejected(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, _ = self.full_case("FK1")
        with self.assertRaises(IntegrityError):
            candidate_repo.create_candidate(org_id, 987654, "ghost", {"crosslinker_ratio": 0.1})

    def test_prediction_for_a_nonexistent_candidate_is_rejected(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, cid = self.full_case("FK2")
        dataset = self.datasets(org_id, case_id)[0]
        with self.assertRaises(IntegrityError):
            candidate_repo.create_prediction(org_id, 987654, dataset["id"], "gp-rank-v1", 0.5, 1.0)

    def test_prediction_for_a_nonexistent_dataset_is_rejected(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, cid = self.full_case("FK3")
        with self.assertRaises(IntegrityError):
            candidate_repo.create_prediction(org_id, cid, 987654, "gp-rank-v1", 0.5, 1.0)

    def test_outcome_for_a_nonexistent_candidate_is_rejected(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, cid = self.full_case("FK4")
        with self.assertRaises(IntegrityError):
            candidate_repo.create_outcome(org_id, 987654, None, "result", True, 1)

    def test_outcome_referencing_a_nonexistent_experiment_is_rejected(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, cid = self.full_case("FK5")
        with self.assertRaises(IntegrityError):
            candidate_repo.create_outcome(org_id, cid, 987654, "result", True, 1)

    def test_dataset_row_for_a_nonexistent_dataset_is_rejected(self):
        from backend.app.repositories import qualification_dataset_repo as repo
        org_id, client, case_id, cid = self.full_case("FK6")
        with self.assertRaises(IntegrityError):
            repo.bulk_create_qualification_experiments(
                org_id, case_id, 987654, [{"features": {"crosslinker_ratio": 0.1}, "target_value": 1.0}])

    def test_membership_for_a_nonexistent_user_or_organization_is_rejected(self):
        from backend.app.repositories import memberships_repo
        org_id, client, case_id, cid = self.full_case("FK7")
        with self.assertRaises(IntegrityError):
            memberships_repo.create_membership(987654, org_id, "member")
        with self.assertRaises(IntegrityError):
            memberships_repo.create_membership(1, 987654, "member")

    # ------------------------------------------------------------------ designed delete rules
    def test_the_declared_delete_rules_are_what_the_schema_says(self):
        """Documents the relationships the application relies on. A change here is a design change
        and must be made deliberately (with a migration), not by accident."""
        def rules(table):
            with self.conn().connect() as c:
                rows = c.execute(text(f"PRAGMA foreign_key_list({table})")).all()
            return {(r[2], r[3]): r[6] for r in rows}  # (parent table, column) -> on_delete
        self.assertEqual(rules("candidate_substitutes")[("change_cases", "change_case_id")], "CASCADE")
        self.assertEqual(rules("predictions")[("candidate_substitutes", "candidate_id")], "CASCADE")
        self.assertEqual(rules("predictions")[("qualification_datasets", "dataset_version_id")], "RESTRICT")
        self.assertEqual(rules("recommended_experiments")[("candidate_substitutes", "candidate_id")], "CASCADE")
        self.assertEqual(rules("qualification_outcomes")[("candidate_substitutes", "candidate_id")], "CASCADE")
        self.assertEqual(rules("qualification_outcomes")[("recommended_experiments", "recommended_experiment_id")], "SET NULL")
        self.assertEqual(rules("qualification_experiments")[("qualification_datasets", "qualification_dataset_id")], "CASCADE")

    def test_a_dataset_that_predictions_were_made_from_cannot_be_deleted(self):
        org_id, client, case_id, cid = self.full_case("FK8")
        dataset = self.datasets(org_id, case_id)[0]
        with self.assertRaises(IntegrityError):
            with self.conn().begin() as c:
                c.execute(text("DELETE FROM qualification_datasets WHERE id = :id"), {"id": dataset["id"]})
        self.assertEqual(self.count("SELECT COUNT(*) FROM qualification_datasets WHERE id = :id", id=dataset["id"]), 1)
        self.assertGreater(len(self.experiments(org_id, dataset["id"])), 0)

    def test_deleting_a_candidate_cascades_to_its_predictions_and_experiments(self):
        org_id, client, case_id, cid = self.full_case("FK9")
        self.assertEqual(self.count("SELECT COUNT(*) FROM predictions WHERE candidate_id = :c", c=cid), 1)
        with self.conn().begin() as c:
            c.execute(text("DELETE FROM candidate_substitutes WHERE id = :id"), {"id": cid})
        self.assertEqual(self.count("SELECT COUNT(*) FROM predictions WHERE candidate_id = :c", c=cid), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM recommended_experiments WHERE candidate_id = :c", c=cid), 0)

    def test_deleting_a_recommended_experiment_keeps_the_outcome_and_clears_the_reference(self):
        from backend.app.repositories import candidate_repo
        org_id, client, case_id, cid = self.full_case("FK10")
        exp = candidate_repo.list_recommended_experiments_for_candidate(org_id, cid)[0]
        r = client.post(f"/api/change-cases/{case_id}/candidates/{cid}/outcome", json={
            "recommended_experiment_id": exp["id"], "actual_result": "310 h", "passed_spec": True})
        self.assertEqual(r.status_code, 201, r.text)
        with self.conn().begin() as c:
            c.execute(text("DELETE FROM recommended_experiments WHERE id = :id"), {"id": exp["id"]})
        self.assertEqual(self.count("SELECT COUNT(*) FROM qualification_outcomes WHERE candidate_id = :c", c=cid), 1)
        self.assertIsNone(self.count("SELECT recommended_experiment_id FROM qualification_outcomes WHERE candidate_id = :c", c=cid))

    def test_deleting_a_change_case_cascades_through_its_whole_tree(self):
        org_id, client, case_id, cid = self.full_case("FK11")
        with self.conn().begin() as c:
            c.execute(text("DELETE FROM change_cases WHERE id = :id"), {"id": case_id})
        for table, col in (("candidate_substitutes", "change_case_id"), ("qualification_datasets", "change_case_id"),
                           ("qualification_experiments", "change_case_id")):
            self.assertEqual(self.count(f"SELECT COUNT(*) FROM {table} WHERE {col} = :id", id=case_id), 0, table)
        self.assertEqual(self.count("SELECT COUNT(*) FROM predictions WHERE candidate_id = :c", c=cid), 0)

    def test_deleting_a_user_keeps_their_records_and_clears_the_reference(self):
        org_id, client, case_id, cid = self.full_case("FK12")
        owner = self.count("SELECT created_by_user_id FROM change_cases WHERE id = :id", id=case_id)
        self.assertIsNotNone(owner)
        with self.conn().begin() as c:
            c.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": owner})
            c.execute(text("DELETE FROM users WHERE id = :u"), {"u": owner})
        self.assertEqual(self.count("SELECT COUNT(*) FROM change_cases WHERE id = :id", id=case_id), 1)
        self.assertIsNone(self.count("SELECT created_by_user_id FROM change_cases WHERE id = :id", id=case_id))

    # ------------------------------------------------------------------ the application keeps working
    def test_normal_workflow_is_unaffected(self):
        org_id, client, case_id, cid = self.full_case("FK13")
        listed = client.get(f"/api/change-cases/{case_id}/candidates").json()
        self.assertIsNotNone(listed[0]["latest_prediction"])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/report").status_code, 200)


if __name__ == "__main__":
    unittest.main()