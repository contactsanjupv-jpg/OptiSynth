"""Level 3: program workspace (details, requirements register, owner and reviewers, milestones, tasks, comments,
completion gate, overdue issues, migration).
Run:  python3 -m unittest backend.tests.test_program_workspace -v"""
import os
import unittest
import uuid
from datetime import date, timedelta

from alembic import command
from sqlalchemy import create_engine, event, text

from backend.app.schemas.errors import ValidationError
from backend.app.services import program_workspace_rules as rules
from backend.tests import test_diagnostic_lifecycle as lc
from backend.tests.test_migration_portability import _config, _UseDatabase

REQ = {"property": "Adhesion", "threshold": 4.0, "direction": "maximize", "unit": "MPa", "test_method": "ISO 4624",
       "criticality": "critical", "source": "Customer specification"}


class TestRules(unittest.TestCase):
    def test_requirement_validation(self):
        self.assertEqual(rules.clean_requirement(REQ)["threshold"], 4.0)
        for bad in ({**REQ, "threshold": float("nan")}, {**REQ, "threshold": True}, {**REQ, "direction": "up"},
                    {**REQ, "criticality": "vital"}, {**REQ, "property": "  "}, {**REQ, "unit": "x" * 41}):
            with self.assertRaises(ValidationError):
                rules.clean_requirement(bad)

    def test_dates(self):
        self.assertEqual(rules.clean_date("2027-03-01", "d"), "2027-03-01")
        for bad in ("01/03/2027", "2027-13-01", "1999-01-01"):
            with self.assertRaises(ValidationError):
                rules.clean_date(bad, "d")
        with self.assertRaises(ValidationError):
            rules.clean_date("", "d", required=True)

    def test_support_never_treats_missing_evidence_as_passing(self):
        target = {"id": 1, "is_ranking_target": 1, "criticality": "critical", "property": "A"}
        other = {"id": 2, "is_ranking_target": 0, "criticality": "critical", "property": "B"}
        self.assertEqual(rules.unsupported_critical([target, other], 0, {}), ["A", "B"])
        self.assertEqual(rules.unsupported_critical([target, other], 1, {2: False}), ["B"])
        self.assertEqual(rules.unsupported_critical([target, other], 1, {2: True}), [])

    def test_completion_gate(self):
        self.assertIsNone(rules.completion_problem(False, [], "intake"))
        self.assertIsNone(rules.completion_problem(True, ["A"], "rejected"))
        self.assertIn("A", rules.completion_problem(True, ["A"], "engineering_review"))
        self.assertIn("approval", rules.completion_problem(True, [], "engineering_review"))
        self.assertIsNone(rules.completion_problem(True, [], "approved"))


class Base(lc.LifecycleBase):
    dir_name = "_test_env_workspace"

    def ws(self, client, case_id):
        r = client.get(f"/api/change-cases/{case_id}/workspace")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def add_req(self, client, case_id, **kw):
        r = client.post(f"/api/change-cases/{case_id}/requirements", json={**REQ, **kw})
        self.assertEqual(r.status_code, 201, r.text)
        return max(x["id"] for x in r.json()["requirements"])

    def person(self, client):
        return client.get("/api/auth/me").json()["user"]["id"]


class TestWorkspace(Base):
    dir_name = "_test_env_workspace_api"

    def test_a_new_program_has_its_ranking_target_in_the_register_and_the_creator_as_owner(self):
        org, client, case_id = self.setup_case("Reg")
        w = self.ws(client, case_id)
        (target,) = w["requirements"]
        self.assertEqual((target["property"], target["threshold"], target["direction"], target["unit"], target["is_ranking_target"]),
                         ("salt_spray_hours", 300.0, "maximize", "h", True))
        self.assertEqual(w["owner"]["id"], self.person(client))
        self.assertEqual(target["support"], "no_result")

    def test_creating_with_details_owner_and_target_metadata(self):
        org, client = self.org_and_client("Details")
        member = self.member_client(client, "member")
        r = client.post("/api/change-cases", json={
            "name": "Full", "trigger_type": "regulatory_restriction", "qualification_spec": lc.SPEC,
            "material_replaced": "Fluorosurfactant X", "reason_for_substitution": "Regulation", "target_date": "2027-06-30",
            "owner_user_id": self.person(member), "target_criticality": "critical", "target_test_method": "ASTM B117"})
        self.assertEqual(r.status_code, 201, r.text)
        w = self.ws(client, r.json()["id"])
        self.assertEqual((w["program"]["material_replaced"], w["program"]["target_date"], w["owner"]["id"]),
                         ("Fluorosurfactant X", "2027-06-30", self.person(member)))
        self.assertEqual((w["requirements"][0]["criticality"], w["requirements"][0]["test_method"]), ("critical", "ASTM B117"))
        self.assertIn("You are the program owner", [n["title"] for n in member.get("/api/notifications").json()["items"]])
        bad = client.post("/api/change-cases", json={"name": "x", "trigger_type": "regulatory_restriction", "qualification_spec": lc.SPEC,
                                                      "owner_user_id": 99999})
        self.assertEqual(bad.status_code, 400)

    def test_requirements_add_edit_archive_and_duplicates(self):
        org, client, case_id = self.setup_case("Req")
        rid = self.add_req(client, case_id, criticality="important")
        dup = client.post(f"/api/change-cases/{case_id}/requirements", json=REQ)
        self.assertEqual(dup.status_code, 400)
        r = client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"threshold": 5.0, "criticality": "informational"})
        self.assertEqual(r.status_code, 200, r.text)
        row = next(x for x in r.json()["requirements"] if x["id"] == rid)
        self.assertEqual((row["threshold"], row["criticality"]), (5.0, "informational"))
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive").status_code, 200)
        self.assertEqual(len(self.ws(client, case_id)["requirements"]), 1)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore").status_code, 200)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"bogus": 1}).status_code, 422)

    def test_ranking_target_stays_in_sync_with_the_spec_and_keeps_its_lock(self):
        org, client, case_id, ids = self.case_with_evidence("Sync", candidates=1)
        target = self.ws(client, case_id)["requirements"][0]
        r = client.patch(f"/api/change-cases/{case_id}/requirements/{target['id']}", json={"threshold": 250})
        self.assertEqual(r.status_code, 200, r.text)
        import json
        self.assertEqual(json.loads(self.get(client, case_id)["qualification_spec_json"])["target_value"], 250.0)
        client.patch(f"/api/change-cases/{case_id}", json={"target_value": 260})  # the older route updates the register too
        self.assertEqual(self.ws(client, case_id)["requirements"][0]["threshold"], 260.0)
        for body in ({"property": "other"}, {"unit": "d"}):
            self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{target['id']}", json=body).status_code, 400)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements/{target['id']}/archive").status_code, 400)
        client.post(f"/api/change-cases/{case_id}/rank")
        locked = client.patch(f"/api/change-cases/{case_id}/requirements/{target['id']}", json={"threshold": 100})
        self.assertEqual(locked.status_code, 400)
        self.assertEqual(self.ws(client, case_id)["requirements"][0]["threshold"], 260.0)
        ok = client.patch(f"/api/change-cases/{case_id}/requirements/{target['id']}", json={"criticality": "critical"})
        self.assertEqual(ok.status_code, 200)

    def test_results_lock_the_measured_fields_and_never_apply_to_the_ranking_target(self):
        org, client, case_id = self.setup_case("Res")
        target = self.ws(client, case_id)["requirements"][0]
        r = client.post(f"/api/change-cases/{case_id}/requirements/{target['id']}/results", json={"result_text": "x", "met": True})
        self.assertEqual(r.status_code, 400)
        rid = self.add_req(client, case_id)
        r = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results",
                        json={"result_text": "4.6 MPa", "met": True, "test_reference": "Lab report 12"})
        self.assertEqual(r.status_code, 201, r.text)
        row = next(x for x in r.json()["requirements"] if x["id"] == rid)
        self.assertEqual((row["support"], row["latest_result"]["text"], row["result_count"]), ("met", "4.6 MPa", 1))
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"threshold": 1.0}).status_code, 400)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"test_method": "Other"}).status_code, 200)
        r = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results", json={"result_text": "3.1 MPa", "met": False})
        self.assertEqual(next(x for x in r.json()["requirements"] if x["id"] == rid)["support"], "not_met")

    def test_owner_reviewers_and_permissions(self):
        org, client, case_id = self.setup_case("Own")
        member = self.member_client(client, "member")
        mid = self.person(member)
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": mid}).status_code, 403)
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/requirements", json=REQ).status_code, 403)
        r = client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": mid})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual([x["id"] for x in r.json()["reviewers"]], [mid])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": mid}).status_code, 400)
        self.assertIn("You were added as a reviewer", [n["title"] for n in member.get("/api/notifications").json()["items"]])
        self.assertEqual(client.put(f"/api/change-cases/{case_id}/owner", json={"user_id": mid}).status_code, 200)
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/requirements", json=REQ).status_code, 201)  # the owner may manage
        _, other = self.org_and_client("Outsider")
        self.assertEqual(client.put(f"/api/change-cases/{case_id}/owner", json={"user_id": self.person(other)}).status_code, 400)
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}/reviewers/{mid}").status_code, 200)
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}/reviewers/{mid}").status_code, 404)

    def test_milestones_and_tasks_and_overdue_issues(self):
        org, client, case_id = self.setup_case("Due")
        past = (date.today() - timedelta(days=3)).isoformat()
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        r = client.post(f"/api/change-cases/{case_id}/milestones", json={"title": "Evidence complete", "due_date": past})
        self.assertEqual(r.status_code, 201, r.text)
        mid = r.json()["milestones"][0]["id"]
        self.assertTrue(r.json()["milestones"][0]["overdue"])
        issues = {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]}
        self.assertIn("overdue_milestone", issues)
        r = client.patch(f"/api/change-cases/{case_id}/milestones/{mid}", json={"completed": True})
        self.assertFalse(r.json()["milestones"][0]["overdue"])
        self.assertNotIn("overdue_milestone", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]})
        member = self.member_client(client, "member")
        r = client.post(f"/api/change-cases/{case_id}/tasks", json={"title": "Review adhesion data", "assignee_user_id": self.person(member), "due_date": past})
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["tasks"][0]["id"]
        self.assertIn("overdue_task", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]})
        self.assertEqual(member.patch(f"/api/change-cases/{case_id}/tasks/{tid}", json={"completed": True}).status_code, 200)
        self.assertNotIn("overdue_task", {a["type"] for a in client.get("/api/portfolio/overview").json()["actions"]})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/milestones", json={"title": "x", "due_date": "soon"}).status_code, 400)
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}/milestones/{mid}").status_code, 200)
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}/tasks/{tid}").status_code, 200)
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}/tasks/{tid}").status_code, 404)

    def test_comments_decisions_and_timeline(self):
        org, client, case_id = self.setup_case("Talk")
        member = self.member_client(client, "member")
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/comments", json={"body": "Looks good"}).status_code, 201)
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/comments", json={"kind": "decision", "body": "Go"}).status_code, 400)
        client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": self.person(member)})
        r = member.post(f"/api/change-cases/{case_id}/comments", json={"kind": "decision", "body": "Proceed to validation"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual([c["kind"] for c in r.json()], ["decision", "comment"])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/comments", json={"body": "  "}).status_code, 400)
        labels = [t["label"] for t in client.get(f"/api/change-cases/{case_id}/timeline").json()]
        for expected in ("Program created", "Reviewer added", "Comment added", "Decision recorded"):
            self.assertIn(expected, labels)
        other_case = self.make_case(client, lc.SPEC)
        self.assertNotIn("Comment added", [t["label"] for t in client.get(f"/api/change-cases/{other_case}/timeline").json()])

    def test_the_completion_gate_and_approval_gate_for_critical_requirements(self):
        org, client, case_id, ids = self.case_with_evidence("Gate", candidates=1, rank=True)
        rid = self.add_req(client, case_id)
        for s in ("requirements", "evidence_collection", "evidence_review"):
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        for s in ("candidate_assessment", "experiment_planning", "physical_validation"):
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
        client.post(f"/api/change-cases/{case_id}/candidates/{ids[0]}/outcome", json={"actual_result": "330 h", "passed_spec": True})
        client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "engineering_review"})
        blocked = client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "approved", "reason": "Met the target"})
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Adhesion", blocked.json()["error"])
        self.assertIn("Adhesion", self.ws(client, case_id)["completion"]["missing_critical"])
        done = client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        self.assertEqual(done.status_code, 400)
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results", json={"result_text": "4.6 MPa", "met": True})
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"}).status_code, 400)  # no decision yet
        ok = client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "approved", "reason": "Met every requirement"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"}).status_code, 200)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements", json={**REQ, "property": "Gloss"}).status_code, 400)  # closed

    def reach_engineering_review(self, client, case_id, ids):
        for s in ("requirements", "evidence_collection", "evidence_review"):
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        for s in ("candidate_assessment", "experiment_planning", "physical_validation"):
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
        client.post(f"/api/change-cases/{case_id}/candidates/{ids[0]}/outcome", json={"actual_result": "330 h", "passed_spec": True})
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "engineering_review"}).status_code, 200)

    def test_an_unmet_critical_requirement_cannot_be_hidden_to_bypass_approval_or_completion(self):
        org, client, case_id, ids = self.case_with_evidence("Hide", candidates=1, rank=True)
        rid = self.add_req(client, case_id)
        self.reach_engineering_review(client, case_id, ids)
        approve = {"to_stage": "approved", "reason": "Met the target"}
        # the direct way out: archiving is refused for a critical requirement
        r = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive")
        self.assertEqual(r.status_code, 400)
        self.assertIn("cannot be archived", r.json()["error"])
        # the indirect way out: lowering the criticality of an unmet requirement is refused too
        for level in ("important", "informational"):
            r = client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": level})
            self.assertEqual(r.status_code, 400, level)
        w = self.ws(client, case_id)
        row = next(x for x in w["requirements"] if x["id"] == rid)
        self.assertEqual((row["criticality"], row["support"]), ("critical", "no_result"))
        self.assertEqual(w["completion"]["missing_critical"], ["Adhesion"])
        # approval and completion are both still blocked afterwards
        blocked = client.post(f"/api/change-cases/{case_id}/stage", json=approve)
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Adhesion", blocked.json()["error"])
        done = client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        self.assertEqual(done.status_code, 400)
        self.assertIn("Adhesion", done.json()["error"])
        # a result that did not meet it changes nothing
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results", json={"result_text": "2.0 MPa", "met": False})
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": "important"}).status_code, 400)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/stage", json=approve).status_code, 400)
        # once a result has met it, the obligation is satisfied and the requirement may be changed
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results", json={"result_text": "4.6 MPa", "met": True})
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": "important"}).status_code, 200)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive").status_code, 200)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/stage", json=approve).status_code, 200)

    def test_an_archived_requirement_that_comes_back_counts_again(self):
        org, client, case_id = self.setup_case("Back")
        rid = self.add_req(client, case_id, criticality="important")
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive")
        self.assertEqual(self.ws(client, case_id)["completion"]["missing_critical"], [])
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore")
        r = client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": "critical"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.ws(client, case_id)["completion"]["missing_critical"], ["Adhesion"])

    def test_archived_requirements_are_listed_and_can_be_restored_with_permissions(self):
        org, client, case_id = self.setup_case("Restore")
        member = self.member_client(client, "member")
        rid = self.add_req(client, case_id, criticality="important")
        self.assertEqual(self.ws(client, case_id)["archived_requirements"], [])
        r = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive")
        w = r.json()
        self.assertEqual([x["id"] for x in w["requirements"]], [x["id"] for x in w["requirements"] if x["id"] != rid])
        (arch,) = w["archived_requirements"]
        self.assertEqual((arch["id"], arch["property"], arch["criticality"]), (rid, "Adhesion", "important"))
        self.assertTrue(arch["archived_at"])
        self.assertEqual(self.ws(member, case_id)["archived_requirements"][0]["id"], rid)  # everyone can see it
        self.assertFalse(self.ws(member, case_id)["permissions"]["can_manage"])
        self.assertEqual(member.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore").status_code, 403)
        # a duplicate of an archived requirement can be added, but then the archived one cannot come back beside it
        self.add_req(client, case_id, criticality="informational")
        dup = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore")
        self.assertEqual(dup.status_code, 400)
        self.assertIn("already exists", dup.json()["error"])
        other = next(x for x in self.ws(client, case_id)["requirements"] if x["property"] == "Adhesion")
        client.post(f"/api/change-cases/{case_id}/requirements/{other['id']}/archive")
        ok = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn(rid, [x["id"] for x in ok.json()["requirements"]])
        self.assertNotIn(rid, [x["id"] for x in ok.json()["archived_requirements"]])
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore").status_code, 400)  # not archived
        self.assertIn("Requirement restored", [t["label"] for t in client.get(f"/api/change-cases/{case_id}/timeline").json()])

    def test_restore_is_read_only_on_archived_and_closed_programs_and_across_organizations(self):
        org, client, case_id = self.setup_case("RO")
        rid = self.add_req(client, case_id, criticality="important")
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive")
        _, other = self.org_and_client("Other")
        self.assertEqual(other.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore").status_code, 404)
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"})
        closed = client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore")
        self.assertEqual(closed.status_code, 400)
        self.assertFalse(self.ws(client, case_id)["permissions"]["can_manage"])
        self.assertEqual(len(self.ws(client, case_id)["archived_requirements"]), 1)  # still listed, read only
        org2, c2, case2 = self.setup_case("RO2")
        r2 = self.add_req(c2, case2, criticality="important")
        c2.post(f"/api/change-cases/{case2}/requirements/{r2}/archive")
        c2.post(f"/api/change-cases/{case2}/archive")
        self.assertEqual(c2.post(f"/api/change-cases/{case2}/requirements/{r2}/restore").status_code, 400)

    def test_a_restored_requirement_counts_again_in_approval_and_completion(self):
        org, client, case_id, ids = self.case_with_evidence("RestoreGate", candidates=1, rank=True)
        rid = self.add_req(client, case_id, criticality="important")
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive")
        self.reach_engineering_review(client, case_id, ids)
        approve = {"to_stage": "approved", "reason": "Met the target"}
        # archived: not counted, even when made critical later it first has to come back
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore")
        r = client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": "critical"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.ws(client, case_id)["completion"]["missing_critical"], ["Adhesion"])
        blocked = client.post(f"/api/change-cases/{case_id}/stage", json=approve)
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("Adhesion", blocked.json()["error"])
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"}).status_code, 400)
        # the critical protections still hold for a restored requirement
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive").status_code, 400)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": "important"}).status_code, 400)
        self.assertEqual(self.ws(client, case_id)["archived_requirements"], [])

    def test_a_requirement_with_an_unmet_result_stays_unmet_after_archive_and_restore(self):
        org, client, case_id = self.setup_case("Roundtrip")
        rid = self.add_req(client, case_id, criticality="important")
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results", json={"result_text": "2.0 MPa", "met": False})
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/archive")
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/restore")
        client.patch(f"/api/change-cases/{case_id}/requirements/{rid}", json={"criticality": "critical"})
        row = next(x for x in self.ws(client, case_id)["requirements"] if x["id"] == rid)
        self.assertEqual((row["support"], row["criticality"]), ("not_met", "critical"))
        self.assertEqual(self.ws(client, case_id)["completion"]["missing_critical"], ["Adhesion"])

    def test_the_timeline_matches_whole_program_ids_only(self):
        import json
        from backend.app.repositories import change_case_repo
        from backend.app.services import audit_service
        org, client = self.org_and_client("Boundary")
        user = self.person(client)
        ids = []
        while True:  # create programs until one id ends with another's, preferring 12 and 112
            ids.append(change_case_repo.create_change_case(org, user, f"P{len(ids)}", "other", None, json.dumps({})))
            pairs = [(a, b) for a in ids for b in ids if a != b and str(b).endswith(str(a))]
            if (12 in ids and 112 in ids) or (pairs and 112 in ids) or len(ids) >= 600:
                break
        self.assertTrue(pairs, "the test needs two program ids where one ends with the other")
        short, long_ = (12, 112) if (12, 112) in pairs else pairs[-1]
        log = lambda detail: audit_service.log(org, user, "change_case.comment_add", detail=detail)
        mine, theirs = [], []
        from backend.app.config.database import db_connection

        def last_id():
            with db_connection() as conn:
                return conn.execute(text("SELECT MAX(id) FROM audit_logs")).scalar()

        for target, bucket, other, other_bucket in ((short, mine, long_, theirs), (long_, theirs, short, mine)):
            for detail in (f"change_case_id={target}", f"change_case_id={target} comment_id=1", f"requirement_id=5 change_case_id={target}",
                           f"x change_case_id={target} y"):
                log(detail); bucket.append(last_id())
        # near misses that must not match either program
        noise = []
        for detail in (f"other_change_case_id={short}", f"other_change_case_id={long_}", f"change_case_id={short}7", f"change_case_id=9{short} x",
                       f"xchange_case_id={long_}", f"changeXcaseXid={short}", f"change_case_id={short}{short}"):
            log(detail); noise.append(last_id())
        got_short = {e["id"] for e in client.get(f"/api/change-cases/{short}/timeline").json()}
        got_long = {e["id"] for e in client.get(f"/api/change-cases/{long_}/timeline").json()}
        self.assertTrue(set(mine) <= got_short, "the shorter id keeps every supported detail format")
        self.assertTrue(set(theirs) <= got_long, "the longer id keeps every supported detail format")
        self.assertFalse(got_short & set(theirs), "the timeline of the shorter id leaked events of the longer id")
        self.assertFalse(got_long & set(mine), "the timeline of the longer id leaked events of the shorter id")
        self.assertFalse((got_short | got_long) & set(noise), "a near miss matched")

    def test_a_rejected_program_can_be_closed_and_programs_without_critical_requirements_are_unchanged(self):
        org, client, case_id = self.setup_case("Plain")
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"}).status_code, 200)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"}).status_code, 200)

    def test_archived_programs_are_read_only_and_organizations_are_isolated(self):
        org, client, case_id = self.setup_case("Iso")
        _, other = self.org_and_client("Other")
        for r in (other.get(f"/api/change-cases/{case_id}/workspace"), other.post(f"/api/change-cases/{case_id}/requirements", json=REQ),
                  other.get(f"/api/change-cases/{case_id}/timeline"), other.post(f"/api/change-cases/{case_id}/comments", json={"body": "x"}),
                  other.post(f"/api/change-cases/{case_id}/milestones", json={"title": "x", "due_date": "2027-01-01"})):
            self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client_factory().get(f"/api/change-cases/{case_id}/workspace").status_code, 401)
        client.post(f"/api/change-cases/{case_id}/archive")
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/requirements", json=REQ).status_code, 400)
        self.assertEqual(client.post(f"/api/change-cases/{case_id}/comments", json={"body": "x"}).status_code, 400)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/workspace").status_code, 200)

    def test_deleting_a_program_removes_everything_under_it(self):
        from backend.app.config.database import db_connection
        org, client, case_id = self.setup_case("Del")
        rid = self.add_req(client, case_id)
        client.post(f"/api/change-cases/{case_id}/requirements/{rid}/results", json={"result_text": "ok", "met": True})
        client.post(f"/api/change-cases/{case_id}/milestones", json={"title": "m", "due_date": "2027-01-01"})
        client.post(f"/api/change-cases/{case_id}/tasks", json={"title": "t"})
        client.post(f"/api/change-cases/{case_id}/comments", json={"body": "c"})
        client.post(f"/api/change-cases/{case_id}/reviewers", json={"user_id": self.person(client)})
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}").status_code, 200)
        with db_connection() as conn:
            for table in ("program_requirements", "program_milestones", "program_tasks", "program_comments", "program_reviewers"):
                self.assertEqual(conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE change_case_id = :c"), {"c": case_id}).scalar(), 0, table)
            self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM requirement_results WHERE requirement_id = :r"), {"r": rid}).scalar(), 0)


class TestFrontendWiring(unittest.TestCase):
    """Source checks: a page that is blank or a control with no backend would not be caught by a status code test."""
    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))

    def read(self, rel):
        with open(os.path.join(self.ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_every_workspace_api_call_has_a_backend_route(self):
        import re
        os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
        os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")
        from backend.app.main import create_app
        routes = {(m, r.path) for r in create_app().routes for m in getattr(r, "methods", None) or ()}
        src = self.read("lib/api/workspace.ts")
        calls = re.findall(r"`\$\{base\(id\)\}([^`]*)`(?:, \{ method: \"(\w+)\")?", src)
        self.assertGreaterEqual(len(calls), 18)
        for path, method in calls:
            template = re.sub(r"\$\{[^}]+\}", "{x}", "/api/change-cases/{change_case_id}" + path)
            pattern = re.compile("^" + re.sub(r"\\\{[^}]+\\\}|\{[^}]+\}", "[^/]+", re.escape(template).replace("\\{x\\}", "{x}")) + "$")
            found = any(m == (method or "GET") and re.match(r"^" + re.sub(r"\{[^}]+\}", "[^/]+", rp) + "$", template.replace("{x}", "1").replace("{change_case_id}", "1"))
                        for m, rp in routes)
            self.assertTrue(found, f"no backend route for {(method or 'GET')} {path}")

    def test_the_program_page_has_every_tab_and_uses_the_auth_hook_correctly(self):
        page = self.read("app/programs/[id]/page.tsx")
        for label in ("Overview", "Requirements", "Evidence", "Candidates", "Analysis", "Experiments", "Physical results",
                      "Qualification", "Reports", "Activity"):
            self.assertIn(f'label: "{label}"', page)
        self.assertIn("const { checking } = useRequireAuth()", page)
        self.assertIn("<Suspense", page)  # useSearchParams needs it for the production build
        tab = self.read("components/programs/RequirementsTab.tsx")
        for needle in ("Archived requirements", "restoreRequirement", "archived_requirements", ">Restore<"):
            self.assertIn(needle, tab)
        self.assertIn("restoreRequirement", self.read("lib/api/workspace.ts"))
        for rel in ("OverviewTab", "RequirementsTab", "ActivityTab", "LaterLevelPanel"):
            self.assertTrue(os.path.isfile(os.path.join(self.ROOT, "components/programs", rel + ".tsx")), rel)

    def test_workspace_copy_has_no_banned_punctuation(self):
        import re
        for rel in ("OverviewTab", "RequirementsTab", "ActivityTab", "LaterLevelPanel", "shared"):
            path = "components/programs/" + rel + (".ts" if rel == "shared" else ".tsx")
            text = self.read(path)
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
            for banned in ("\u2014", "\u2013", " -- ", " \u00b7 "):
                self.assertNotIn(banned, text, path)
            self.assertNotRegex(text, r"(?i)diagnostic")


class TestMigration(unittest.TestCase):
    def setUp(self):
        import shutil
        self.dir = os.path.join(os.path.dirname(__file__), f"_test_env_ws_mig_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.url = f"sqlite:///{self.dir}/m.db"

    def test_existing_programs_get_a_ranking_target_row_and_downgrade_restores_the_schema(self):
        engine = create_engine(self.url)

        @event.listens_for(engine, "connect")
        def _fk(c, r):
            c.execute("PRAGMA foreign_keys=ON")
        spec = '{"feature_columns": ["a"], "target_metric": "hours", "target_value": 300, "direction": "maximize", "units": {"hours": "h"}}'
        with _UseDatabase(self.url):
            command.upgrade(_config(), "b8f4c2d6e1a9")
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('M', 't')"))
                conn.execute(text("INSERT INTO users (email, password_hash, display_name, is_active, created_at) VALUES ('m@example.com', 'h', 'M', 1, 't')"))
                for name, s in (("good", spec), ("empty", "{}")):
                    conn.execute(text("""INSERT INTO change_cases (organization_id, created_by_user_id, name, trigger_type, restricted_substance,
                                         qualification_spec_json, status, created_at, updated_at)
                                         VALUES (1, 1, :n, 'other', 'x', :s, 'draft', 't', 't')"""), {"n": name, "s": s})
            command.upgrade(_config(), "head")
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT change_case_id, property, threshold, direction, unit, is_ranking_target, criticality FROM program_requirements")).all()
                self.assertEqual(rows, [(1, "hours", 300.0, "maximize", "h", 1, "important")])
            command.downgrade(_config(), "b8f4c2d6e1a9")
            with engine.connect() as conn:
                self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM change_cases")).scalar(), 2)
                cols = {r[1] for r in conn.execute(text("PRAGMA table_info(change_cases)"))}
                self.assertFalse({"owner_user_id", "material_replaced", "target_date"} & cols)
            command.upgrade(_config(), "head")
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
