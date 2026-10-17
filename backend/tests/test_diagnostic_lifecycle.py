"""
Level 1 (part C) -- managing a diagnostic: the honest status model, editing, archiving / restoring,
and permanent deletion only when nothing depends on it.

Run:  python3 -m unittest backend.tests.test_diagnostic_lifecycle -v
"""
import json
import os
import unittest
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.schemas.errors import ValidationError  # noqa: E402
from backend.app.services import change_case_lifecycle_rules as rules  # noqa: E402
from backend.tests import evidence_fixtures as F  # noqa: E402
from backend.tests._api_base import SPEC, IsolatedApiTestCase  # noqa: E402

HEAD = ["crosslinker_ratio", "cure_temp_c", "salt_spray_hours"]
PASSWORD = "correct-horse-battery-staple"
NO_USE = {"dataset_count": 0, "candidate_count": 0, "prediction_count": 0, "outcome_count": 0}


class TestLifecycleRules(unittest.TestCase):
    def case(self, **kw):
        return {"status": "draft", "archived_at": None, **kw}

    def test_every_status_has_one_documented_meaning(self):
        self.assertEqual(set(rules.STATUS_MEANINGS), {"draft", "active", "completed"})
        self.assertIn("not a qualification result", rules.STATUS_MEANINGS["active"])
        self.assertIn("cannot be reopened", rules.STATUS_MEANINGS["completed"])

    def test_archived_is_a_marker_not_a_status(self):
        self.assertNotIn("archived", rules.STATUS_MEANINGS)
        self.assertTrue(rules.is_archived({"archived_at": "2026-10-15T00:00:00"}))
        self.assertFalse(rules.is_archived({"archived_at": None}))

    def test_delete_is_allowed_only_when_nothing_depends_on_the_diagnostic(self):
        self.assertIsNone(rules.delete_blocked_reason(self.case(), NO_USE))
        self.assertIsNone(rules.delete_blocked_reason(self.case(status="active"), {**NO_USE, "dataset_count": 3, "candidate_count": 4}))
        self.assertIn("completed", rules.delete_blocked_reason(self.case(status="completed"), NO_USE))
        self.assertIn("ranked", rules.delete_blocked_reason(self.case(), {**NO_USE, "prediction_count": 1}))
        self.assertIn("outcomes", rules.delete_blocked_reason(self.case(), {**NO_USE, "outcome_count": 1}))
        # outcomes are reported first: the stronger reason
        self.assertIn("outcomes", rules.delete_blocked_reason(self.case(), {**NO_USE, "prediction_count": 2, "outcome_count": 1}))

    def test_check_can_delete_raises_with_the_reason_and_the_alternative(self):
        with self.assertRaises(ValidationError) as ctx:
            rules.check_can_delete(self.case(), {**NO_USE, "prediction_count": 1})
        self.assertIn("archive", ctx.exception.message.lower())
        rules.check_can_delete(self.case(), NO_USE)

    def test_only_owner_or_admin_may_delete(self):
        rules.check_role_can_delete("owner")
        rules.check_role_can_delete("admin")
        with self.assertRaises(PermissionError):
            rules.check_role_can_delete("member")

    def test_requirement_lock(self):
        self.assertIsNone(rules.requirement_locked_reason(self.case(), NO_USE))
        self.assertIsNone(rules.requirement_locked_reason(self.case(status="active"), {**NO_USE, "dataset_count": 1, "candidate_count": 2}))
        for case, usage in ((self.case(), {**NO_USE, "prediction_count": 1}), (self.case(), {**NO_USE, "outcome_count": 1}),
                            (self.case(status="completed"), NO_USE), (self.case(archived_at="x"), NO_USE)):
            self.assertIsNotNone(rules.requirement_locked_reason(case, usage), (case, usage))

    def test_check_can_edit(self):
        rules.check_can_edit(self.case(), NO_USE, {"name": "x"})
        rules.check_can_edit(self.case(), NO_USE, {"target_value": 5.0, "direction": "minimize"})
        for case, changes in ((self.case(), {}), (self.case(), {"feature_columns": ["a"]}), (self.case(archived_at="x"), {"name": "x"}),
                              (self.case(status="completed"), {"name": "x"})):
            with self.assertRaises(ValidationError):
                rules.check_can_edit(case, NO_USE, changes)
        with self.assertRaises(ValidationError):  # ranked: metadata ok, requirement locked
            rules.check_can_edit(self.case(), {**NO_USE, "prediction_count": 1}, {"target_value": 1.0})
        rules.check_can_edit(self.case(), {**NO_USE, "prediction_count": 1}, {"name": "renamed"})

    def test_clean_name_and_substance(self):
        self.assertEqual(rules.clean_name("  Coating A  "), "Coating A")
        for bad in ("", "   ", None, 5, "x" * 201):
            with self.assertRaises(ValidationError):
                rules.clean_name(bad)
        self.assertIsNone(rules.clean_substance(None))
        self.assertIsNone(rules.clean_substance("   "))
        self.assertEqual(rules.clean_substance(" PFHxA "), "PFHxA")
        with self.assertRaises(ValidationError):
            rules.clean_substance("x" * 501)


class LifecycleBase(IsolatedApiTestCase):
    dir_name = "_test_env_lifecycle"
    _n = 0

    def setUp(self):
        type(self)._n += 1

    def case_with_evidence(self, label, candidates=1, rank=False):
        org_id, client, case_id = self.setup_case(f"{label}{self._n}", SPEC)
        self.paid(org_id)
        r = self.upload(client, case_id, "h.csv", F.csv_bytes(HEAD, F.base_rows(14)))
        self.assertEqual(r.status_code, 201, r.text)
        ids = []
        for k in range(candidates):
            c = client.post(f"/api/change-cases/{case_id}/candidates",
                            json={"name": f"Cand {k}", "features": {"crosslinker_ratio": 0.09 + 0.01 * k, "cure_temp_c": 150}})
            self.assertEqual(c.status_code, 201, c.text)
            ids.append(c.json()["id"])
        if rank:
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/rank").status_code, 200)
        return org_id, client, case_id, ids

    def get(self, client, case_id):
        r = client.get(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def member_client(self, owner_client, role="member"):
        email = f"lc{type(self)._n}{role}@lifecycle-test.example.com"
        r = owner_client.post("/api/team/invitations", json={"email": email, "role": role})
        self.assertEqual(r.status_code, 201, r.text)
        token = r.json()["invite_url"].split("invite=")[1]
        client = self.client_factory()
        r = client.post("/api/auth/signup", json={"email": email, "password": PASSWORD, "invite_token": token, "display_name": "M"})
        self.assertIn(r.status_code, (200, 201), r.text)
        return client

    def row_counts(self, case_id):
        from sqlalchemy import text
        from backend.app.config import database
        q = {
            "cases": "SELECT COUNT(*) FROM change_cases WHERE id = :c",
            "datasets": "SELECT COUNT(*) FROM qualification_datasets WHERE change_case_id = :c",
            "experiments": "SELECT COUNT(*) FROM qualification_experiments WHERE change_case_id = :c",
            "candidates": "SELECT COUNT(*) FROM candidate_substitutes WHERE change_case_id = :c",
            "predictions": "SELECT COUNT(*) FROM predictions WHERE candidate_id IN (SELECT id FROM candidate_substitutes WHERE change_case_id = :c)",
            "recommended": "SELECT COUNT(*) FROM recommended_experiments WHERE candidate_id IN (SELECT id FROM candidate_substitutes WHERE change_case_id = :c)",
            "outcomes": "SELECT COUNT(*) FROM qualification_outcomes WHERE candidate_id IN (SELECT id FROM candidate_substitutes WHERE change_case_id = :c)",
        }
        with database.engine.connect() as conn:
            return {k: conn.execute(text(sql), {"c": case_id}).scalar() for k, sql in q.items()}

    def stored_files(self, org_id):
        from backend.app.services import qualification_dataset_service as svc
        folder = os.path.join(svc.UPLOAD_ROOT, str(org_id))
        return set(os.listdir(folder)) if os.path.isdir(folder) else set()


class TestStatusModel(LifecycleBase):
    def test_a_new_diagnostic_is_draft_and_not_archived(self):
        _, client, case_id = self.setup_case(f"Status{self._n}", SPEC)
        case = self.get(client, case_id)
        self.assertEqual(case["status"], "draft")
        self.assertIsNone(case["archived_at"])
        self.assertFalse(case["archived"])
        self.assertIn("Setup in progress", case["status_meaning"])

    def test_nothing_but_an_explicit_transition_ever_changes_the_status(self):
        org_id, client, case_id, ids = self.case_with_evidence("Auto", candidates=2, rank=True)
        client.get(f"/api/change-cases/{case_id}")
        client.get(f"/api/change-cases/{case_id}/candidates")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/report").status_code, 200)
        self.assertEqual(self.get(client, case_id)["status"], "draft")  # uploaded, candidates, ranked, reported -- still draft

    def test_explicit_transitions_persist_and_invalid_ones_are_refused(self):
        _, client, case_id = self.setup_case(f"Trans{self._n}", SPEC)
        patch = lambda s: client.patch(f"/api/change-cases/{case_id}/status", json={"status": s})
        self.assertEqual(patch("completed").status_code, 400)       # draft cannot skip to completed
        self.assertEqual(self.get(client, case_id)["status"], "draft")
        self.assertEqual(patch("active").status_code, 200)
        self.assertEqual(self.get(client, case_id)["status"], "active")
        self.assertIn("workflow progress", self.get(client, case_id)["status_meaning"])
        self.assertEqual(patch("draft").status_code, 400)           # no way back
        self.assertEqual(patch("active").status_code, 400)          # not a no-op either
        self.assertEqual(patch("completed").status_code, 200)
        self.assertEqual(self.get(client, case_id)["status"], "completed")
        for s in ("draft", "active", "completed"):
            self.assertEqual(patch(s).status_code, 400)             # a closed record never reopens
        self.assertEqual(patch("archived").status_code, 422)        # archived is not a status

    def test_unknown_status_value_is_rejected(self):
        _, client, case_id = self.setup_case(f"Bad{self._n}", SPEC)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "qualified"}).status_code, 422)


class TestEditing(LifecycleBase):
    def test_name_reason_and_substance_are_saved_in_the_database(self):
        _, client, case_id = self.setup_case(f"Edit{self._n}", SPEC)
        r = client.patch(f"/api/change-cases/{case_id}", json={
            "name": "  Renamed diagnostic  ", "trigger_type": "supplier_loss", "restricted_substance": " Resin X "})
        self.assertEqual(r.status_code, 200, r.text)
        fresh = self.get(client, case_id)  # a new read, not the echoed response
        self.assertEqual((fresh["name"], fresh["trigger_type"], fresh["restricted_substance"]),
                         ("Renamed diagnostic", "supplier_loss", "Resin X"))
        self.assertEqual(fresh["status"], "draft")
        self.assertEqual(json.loads(fresh["qualification_spec_json"])["target_value"], SPEC["target_value"])

    def test_only_the_fields_sent_change(self):
        _, client, case_id = self.setup_case(f"Part{self._n}", SPEC)
        before = self.get(client, case_id)
        client.patch(f"/api/change-cases/{case_id}", json={"name": "Only the name"})
        after = self.get(client, case_id)
        self.assertEqual(after["name"], "Only the name")
        for key in ("trigger_type", "restricted_substance", "qualification_spec_json", "status", "created_at"):
            self.assertEqual(after[key], before[key], key)

    def test_the_restricted_substance_can_be_cleared(self):
        _, client, case_id = self.setup_case(f"Clear{self._n}", SPEC)
        client.patch(f"/api/change-cases/{case_id}", json={"restricted_substance": "PFHxA"})
        self.assertEqual(self.get(client, case_id)["restricted_substance"], "PFHxA")
        for blank in (None, "", "   "):
            client.patch(f"/api/change-cases/{case_id}", json={"restricted_substance": "again"})
            self.assertEqual(client.patch(f"/api/change-cases/{case_id}", json={"restricted_substance": blank}).status_code, 200)
            self.assertIsNone(self.get(client, case_id)["restricted_substance"])

    def test_invalid_edits_are_refused_and_change_nothing(self):
        _, client, case_id = self.setup_case(f"Inv{self._n}", SPEC)
        before = self.get(client, case_id)
        bad = [
            ({}, 400), ({"name": ""}, 400), ({"name": "   "}, 400), ({"name": None}, 422), ({"name": "x" * 201}, 422),
            ({"trigger_type": "because"}, 422), ({"status": "completed"}, 422), ({"id": 99}, 422),
            ({"feature_columns": ["a"]}, 422), ({"target_metric": "z"}, 422), ({"units": {"a": "h"}}, 422),
            ({"target_value": "300"}, 422), ({"target_value": True}, 422), ({"direction": "up"}, 422),
        ]
        for body, status in bad:
            r = client.patch(f"/api/change-cases/{case_id}", json=body)
            self.assertEqual(r.status_code, status, (body, r.text))
        for token in ("NaN", "Infinity"):
            r = client.patch(f"/api/change-cases/{case_id}", content=f'{{"target_value": {token}}}',
                             headers={"Content-Type": "application/json"})
            self.assertIn(r.status_code, (400, 422), token)
        self.assertEqual(self.get(client, case_id), before)

    def test_requirement_threshold_and_direction_can_be_edited_before_rankings_exist(self):
        org_id, client, case_id, ids = self.case_with_evidence("Req", candidates=1, rank=False)  # evidence + candidate, not ranked
        r = client.patch(f"/api/change-cases/{case_id}", json={"target_value": 450, "direction": "minimize"})
        self.assertEqual(r.status_code, 200, r.text)
        spec = json.loads(self.get(client, case_id)["qualification_spec_json"])
        self.assertEqual((spec["target_value"], spec["direction"]), (450.0, "minimize"))
        self.assertEqual(spec["feature_columns"], SPEC["feature_columns"])  # structure untouched
        self.assertEqual(spec["target_metric"], SPEC["target_metric"])

    def test_requirement_is_locked_once_candidates_have_been_ranked(self):
        org_id, client, case_id, ids = self.case_with_evidence("Lock", candidates=1, rank=True)
        before = json.loads(self.get(client, case_id)["qualification_spec_json"])
        r = client.patch(f"/api/change-cases/{case_id}", json={"target_value": 1.0})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("already been ranked", r.json()["error"])
        self.assertEqual(json.loads(self.get(client, case_id)["qualification_spec_json"]), before)
        case = self.get(client, case_id)
        self.assertFalse(case["requirement_editable"])
        self.assertIsNotNone(case["requirement_locked_reason"])
        # the name is still editable: it does not alter what any stored result means
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}", json={"name": "Renamed after ranking"}).status_code, 200)

    def test_a_completed_diagnostic_cannot_be_edited(self):
        _, client, case_id = self.setup_case(f"Done{self._n}", SPEC)
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        r = client.patch(f"/api/change-cases/{case_id}", json={"name": "Too late"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotEqual(self.get(client, case_id)["name"], "Too late")

    def test_edit_requires_a_session_and_the_right_organization(self):
        org_a, a, case_id = self.setup_case(f"EditA{self._n}", SPEC)
        org_b, b = self.org_and_client(f"EditB{self._n}")
        before = self.get(a, case_id)
        self.assertEqual(self.client_factory().patch(f"/api/change-cases/{case_id}", json={"name": "x"}).status_code, 401)
        self.assertEqual(b.patch(f"/api/change-cases/{case_id}", json={"name": "hijacked"}).status_code, 404)
        self.assertEqual(self.get(a, case_id), before)

    def test_edit_is_recorded_in_the_audit_log(self):
        from backend.app.repositories import audit_repo
        org_id, client, case_id = self.setup_case(f"Aud{self._n}", SPEC)
        client.patch(f"/api/change-cases/{case_id}", json={"name": "Audited"})
        client.post(f"/api/change-cases/{case_id}/archive")
        client.post(f"/api/change-cases/{case_id}/restore")
        client.delete(f"/api/change-cases/{case_id}")
        actions = [e["action"] for e in audit_repo.list_audit_logs(org_id)]
        for expected in ("change_case.update", "change_case.archive", "change_case.restore", "change_case.delete"):
            self.assertIn(expected, actions)


class TestArchiving(LifecycleBase):
    def test_archived_diagnostics_leave_the_default_list_but_keep_everything(self):
        org_id, client, case_id, ids = self.case_with_evidence("Arch", candidates=2, rank=True)
        before = self.row_counts(case_id)
        r = client.post(f"/api/change-cases/{case_id}/archive")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["archived"])
        self.assertIsNotNone(r.json()["archived_at"])
        self.assertNotIn(case_id, [c["id"] for c in client.get("/api/change-cases").json()])
        listed = client.get("/api/change-cases?include_archived=true").json()
        self.assertIn(case_id, [c["id"] for c in listed])
        self.assertEqual(self.row_counts(case_id), before)  # nothing deleted
        self.assertEqual(self.get(client, case_id)["status"], "draft")  # status untouched by archiving

    def test_archive_and_restore_round_trip_for_every_status(self):
        for target in ("draft", "active", "completed"):
            _, client, case_id = self.setup_case(f"RT{target}{self._n}", SPEC)
            if target != "draft":
                client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
            if target == "completed":
                client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/archive").status_code, 200, target)
            restored = client.post(f"/api/change-cases/{case_id}/restore")
            self.assertEqual(restored.status_code, 200, target)
            self.assertFalse(restored.json()["archived"])
            self.assertEqual(restored.json()["status"], target)
            self.assertIn(case_id, [c["id"] for c in client.get("/api/change-cases").json()])

    def test_double_archive_and_restore_of_an_unarchived_case_are_refused(self):
        _, client, case_id = self.setup_case(f"Dbl{self._n}", SPEC)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/restore").status_code, 400)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/archive").status_code, 200)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/archive").status_code, 400)

    def test_an_archived_diagnostic_is_read_only(self):
        org_id, client, case_id, ids = self.case_with_evidence("RO", candidates=1, rank=True)
        exp = client.get(f"/api/change-cases/{case_id}/candidates").json()
        client.post(f"/api/change-cases/{case_id}/archive")
        before_counts = self.row_counts(case_id)
        before_case = self.get(client, case_id)
        csv = F.csv_bytes(HEAD, F.base_rows(14))
        attempts = [
            client.patch(f"/api/change-cases/{case_id}", json={"name": "changed"}),
            client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"}),
            client.post(f"/api/change-cases/{case_id}/candidates",
                        json={"name": "New", "features": {"crosslinker_ratio": 0.1, "cure_temp_c": 150}}),
            client.post(f"/api/change-cases/{case_id}/rank"),
            client.post(f"/api/change-cases/{case_id}/dataset", files={"file": ("n.csv", csv, "text/csv")}),
            client.post(f"/api/change-cases/{case_id}/candidates/{ids[0]}/outcome",
                        json={"actual_result": "x", "passed_spec": True}),
        ]
        for r in attempts:
            self.assertEqual(r.status_code, 400, r.text)
            self.assertEqual(r.json()["field"], "archived")
        self.assertEqual(self.row_counts(case_id), before_counts)
        self.assertEqual(self.get(client, case_id)["name"], before_case["name"])
        # reading is unaffected
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/candidates").status_code, 200)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/evidence").status_code, 200)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/report").status_code, 200)
        # restoring re-enables writing
        client.post(f"/api/change-cases/{case_id}/restore")
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}", json={"name": "changed"}).status_code, 200)

    def test_archive_requires_a_session_and_the_right_organization(self):
        org_a, a, case_id = self.setup_case(f"ArA{self._n}", SPEC)
        org_b, b = self.org_and_client(f"ArB{self._n}")
        self.assertEqual(self.client_factory().post(f"/api/change-cases/{case_id}/archive").status_code, 401)
        self.assertEqual(b.post(f"/api/change-cases/{case_id}/archive").status_code, 404)
        self.assertEqual(b.post(f"/api/change-cases/{case_id}/restore").status_code, 404)
        self.assertFalse(self.get(a, case_id)["archived"])

    def test_archiving_is_available_to_any_team_member(self):
        org_id, owner, case_id = self.setup_case(f"ArM{self._n}", SPEC)
        member = self.member_client(owner)
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/archive").status_code, 200)


class TestDeletion(LifecycleBase):
    def test_an_unused_diagnostic_is_deleted_completely(self):
        org_id, client, case_id, ids = self.case_with_evidence("Del", candidates=3, rank=False)
        other_org, other, other_case = self.setup_case(f"Keep{self._n}", SPEC)
        stored = self.datasets(org_id, case_id)[0]["stored_filename"]
        self.assertIn(stored, self.stored_files(org_id))
        listed = next(c for c in client.get("/api/change-cases").json() if c["id"] == case_id)
        self.assertTrue(listed["deletable"])
        self.assertIsNone(listed["delete_blocked_reason"])
        self.assertEqual((listed["dataset_count"], listed["candidate_count"]), (1, 3))

        r = client.delete(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["deleted"], body["datasets_removed"], body["candidates_removed"], body["files_removed"]),
                         (True, 1, 3, 1))
        self.assertTrue(all(v == 0 for v in self.row_counts(case_id).values()), self.row_counts(case_id))  # no orphans
        self.assertNotIn(stored, self.stored_files(org_id))         # the uploaded original is gone too
        self.assertEqual(client.get(f"/api/change-cases/{case_id}").status_code, 404)
        self.assertNotIn(case_id, [c["id"] for c in client.get("/api/change-cases?include_archived=true").json()])
        self.assertEqual(self.get(other, other_case)["id"], other_case)  # a different diagnostic is untouched
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}").status_code, 404)  # second delete: gone

    def test_an_empty_draft_can_be_deleted(self):
        _, client, case_id = self.setup_case(f"Empty{self._n}", SPEC)
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}").status_code, 200)

    def test_a_ranked_diagnostic_cannot_be_deleted_but_can_be_archived(self):
        org_id, client, case_id, ids = self.case_with_evidence("Rk", candidates=2, rank=True)
        before = self.row_counts(case_id)
        r = client.delete(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("ranked", r.json()["error"])
        self.assertIn("rchive", r.json()["error"])
        self.assertEqual(self.row_counts(case_id), before)
        case = self.get(client, case_id)
        self.assertFalse(case["deletable"])
        self.assertIn("ranked", case["delete_blocked_reason"])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/archive").status_code, 200)

    def test_a_diagnostic_with_a_recorded_outcome_is_never_deleted(self):
        org_id, client, case_id, ids = self.case_with_evidence("Out", candidates=1, rank=True)
        r = client.post(f"/api/change-cases/{case_id}/candidates/{ids[0]}/outcome",
                        json={"actual_result": "312 h", "passed_spec": True})
        self.assertEqual(r.status_code, 201, r.text)
        before = self.row_counts(case_id)
        self.assertEqual(before["outcomes"], 1)
        r = client.delete(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 400)
        self.assertIn("outcomes", r.json()["error"])
        self.assertEqual(self.row_counts(case_id), before)

    def test_a_completed_diagnostic_is_never_deleted(self):
        _, client, case_id = self.setup_case(f"Cmp{self._n}", SPEC)
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        r = client.delete(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 400)
        self.assertIn("completed", r.json()["error"])
        self.assertEqual(self.get(client, case_id)["status"], "completed")

    def test_an_archived_unused_diagnostic_can_still_be_deleted(self):
        _, client, case_id = self.setup_case(f"ArD{self._n}", SPEC)
        client.post(f"/api/change-cases/{case_id}/archive")
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}").status_code, 200)

    def test_only_owners_and_admins_may_delete(self):
        org_id, owner, case_id = self.setup_case(f"Role{self._n}", SPEC)
        member = self.member_client(owner, "member")
        r = member.delete(f"/api/change-cases/{case_id}")
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.get(owner, case_id)["id"], case_id)
        admin = self.member_client(owner, "admin")
        self.assertEqual(admin.delete(f"/api/change-cases/{case_id}").status_code, 200)

    def test_delete_requires_a_session_and_the_right_organization(self):
        org_a, a, case_id = self.setup_case(f"DlA{self._n}", SPEC)
        org_b, b = self.org_and_client(f"DlB{self._n}")
        self.assertEqual(self.client_factory().delete(f"/api/change-cases/{case_id}").status_code, 401)
        self.assertEqual(b.delete(f"/api/change-cases/{case_id}").status_code, 404)
        self.assertEqual(self.get(a, case_id)["id"], case_id)

    def test_deletion_is_all_or_nothing(self):
        from backend.app.repositories import change_case_repo
        org_id, client, case_id, ids = self.case_with_evidence("Atom", candidates=2, rank=False)
        before = self.row_counts(case_id)
        stored = self.datasets(org_id, case_id)[0]["stored_filename"]
        real_text = change_case_repo.text

        def failing_text(sql, *a, **kw):
            if "DELETE FROM qualification_experiments" in str(sql):
                raise RuntimeError("simulated failure halfway through the deletion")
            return real_text(sql, *a, **kw)

        with mock.patch.object(change_case_repo, "text", side_effect=failing_text):
            with self.assertRaises(RuntimeError):
                client.delete(f"/api/change-cases/{case_id}")
        self.assertEqual(self.row_counts(case_id), before)   # candidates deleted before the failure were rolled back
        self.assertIn(stored, self.stored_files(org_id))     # and no file was removed
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}").status_code, 200)  # a clean retry works

    def test_deletion_does_not_follow_a_stored_name_out_of_the_organization_folder(self):
        from backend.app.config import database
        from sqlalchemy import text
        org_id, client, case_id, ids = self.case_with_evidence("Trav", candidates=0, rank=False)
        outside = os.path.join(os.path.dirname(self.stored_files_path(org_id)), "keep-me.txt")
        with open(outside, "w") as f:
            f.write("must survive")
        try:
            with database.engine.begin() as c:
                c.execute(text("UPDATE qualification_datasets SET stored_filename = :n WHERE change_case_id = :c"),
                          {"n": "../keep-me.txt", "c": case_id})
            r = client.delete(f"/api/change-cases/{case_id}")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["files_not_removed"], 1)
            self.assertTrue(os.path.exists(outside))
        finally:
            if os.path.exists(outside):
                os.remove(outside)

    def stored_files_path(self, org_id):
        from backend.app.services import qualification_dataset_service as svc
        return os.path.join(svc.UPLOAD_ROOT, str(org_id), "x")


class TestListDescriptions(LifecycleBase):
    def test_list_reports_what_depends_on_each_diagnostic(self):
        org_id, client, case_id, ids = self.case_with_evidence("Desc", candidates=2, rank=True)
        item = next(c for c in client.get("/api/change-cases").json() if c["id"] == case_id)
        self.assertEqual((item["dataset_count"], item["candidate_count"], item["prediction_count"], item["outcome_count"]),
                         (1, 2, 2, 0))
        self.assertFalse(item["deletable"])
        self.assertFalse(item["requirement_editable"])
        self.assertIn("status_meaning", item)

    def test_the_list_never_includes_another_organizations_diagnostics(self):
        org_a, a, case_a = self.setup_case(f"LsA{self._n}", SPEC)
        org_b, b, case_b = self.setup_case(f"LsB{self._n}", SPEC)
        ids_a = [c["id"] for c in a.get("/api/change-cases?include_archived=true").json()]
        ids_b = [c["id"] for c in b.get("/api/change-cases?include_archived=true").json()]
        self.assertIn(case_a, ids_a); self.assertNotIn(case_b, ids_a)
        self.assertIn(case_b, ids_b); self.assertNotIn(case_a, ids_b)


class TestArchivedAtMigration(LifecycleBase):
    dir_name = "_test_env_lifecycle_migration"

    def _columns(self):
        from sqlalchemy import text
        from backend.app.config import database
        with database.db_connection() as conn:
            return [r[1] for r in conn.execute(text("PRAGMA table_info(change_cases)")).all()]

    def _alembic(self, direction, revision):
        from alembic.config import Config
        from alembic import command
        backend_dir = os.path.join(os.path.dirname(__file__), "..")
        cfg = Config(os.path.join(backend_dir, "alembic.ini"))
        cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
        getattr(command, direction)(cfg, revision)

    def test_migration_is_additive_nullable_and_reversible(self):
        org_id, client, case_id = self.setup_case(f"Mig{self._n}", SPEC)
        client.post(f"/api/change-cases/{case_id}/archive")
        self.assertIn("archived_at", self._columns())
        self._alembic("downgrade", "c9e61a4d2b88")
        self.assertNotIn("archived_at", self._columns())
        self._alembic("upgrade", "head")
        self.assertIn("archived_at", self._columns())
        case = self.get(client, case_id)
        self.assertIsNone(case["archived_at"])  # a re-upgraded row is simply not archived: nothing is invented


if __name__ == "__main__":
    unittest.main()