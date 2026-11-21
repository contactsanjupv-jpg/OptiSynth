"""Level 1: program stages (rules, history, permissions, closing) and the portfolio overview (every number comes from
stored records, tenant isolation, range, filters).
Run:  python3 -m unittest backend.tests.test_program_stages_overview -v
(PostgreSQL scenarios: set OPTISYNTH_TEST_POSTGRES_URL as in test_migration_portability.)"""
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from alembic import command

from backend.app.config.settings import settings
from backend.app.schemas.errors import ValidationError
from backend.app.services import portfolio_rules as prules
from backend.app.services import program_stage_rules as srules
from backend.tests import evidence_fixtures as F
from backend.tests import test_diagnostic_lifecycle as lc
from backend.tests.test_migration_portability import POSTGRES_URL, _config, _UseDatabase

FACTS = {"dataset_count": 0, "candidate_count": 0, "prediction_count": 0, "outcome_count": 0, "passed_outcome_count": 0}


def _check(current, target, status="active", archived=False, role="owner", reason="", facts=None):
    return srules.check_transition(current, target, status, archived, role, reason, {**FACTS, **(facts or {})})


class TestStageRules(unittest.TestCase):
    def test_workflow_is_ordered_and_labelled(self):
        self.assertEqual(len(srules.ALL_STAGES), 11)
        self.assertEqual(set(srules.LABELS), set(srules.ALL_STAGES))
        self.assertEqual(srules.label("evidence_review"), "Evidence review")
        for label in srules.LABELS.values():  # sentence case, no punctuation
            self.assertEqual(label, label[0].upper() + label[1:].lower())

    def test_forward_is_one_step_at_a_time(self):
        self.assertEqual(_check("intake", "requirements", status="draft"), "")
        with self.assertRaises(ValidationError):
            _check("intake", "evidence_collection", status="draft")  # skips a step

    def test_going_back_needs_a_reason(self):
        with self.assertRaises(ValidationError) as cm:
            _check("evidence_collection", "requirements", status="draft")
        self.assertEqual(cm.exception.field, "reason")
        self.assertEqual(_check("evidence_collection", "requirements", status="draft", reason="  Scope changed  "), "Scope changed")

    def test_record_checks(self):
        cases = [("evidence_collection", "evidence_review", {}), ("evidence_review", "candidate_assessment", {}),
                 ("candidate_assessment", "experiment_planning", {"dataset_count": 1}),
                 ("experiment_planning", "physical_validation", {}), ("physical_validation", "engineering_review", {"prediction_count": 1})]
        for current, target, facts in cases:
            with self.assertRaises(ValidationError, msg=target):
                _check(current, target, facts=facts)
        _check("evidence_collection", "evidence_review", facts={"dataset_count": 1})
        _check("candidate_assessment", "experiment_planning", facts={"prediction_count": 1})
        _check("physical_validation", "engineering_review", facts={"outcome_count": 1})

    def test_decisions(self):
        facts = {"outcome_count": 1, "passed_outcome_count": 1}
        self.assertEqual(_check("engineering_review", "approved", reason="Met every target", facts=facts), "Met every target")
        _check("engineering_review", "rejected", reason="Failed adhesion", facts={"outcome_count": 1})
        with self.assertRaises(ValidationError):  # approval needs a passing physical result
            _check("engineering_review", "approved", reason="Looks fine", facts={"outcome_count": 1})
        with self.assertRaises(ValidationError):  # a reason is required
            _check("engineering_review", "approved", facts=facts)
        with self.assertRaises(ValidationError):  # members cannot decide
            _check("engineering_review", "approved", role="member", reason="Met every target", facts=facts)
        with self.assertRaises(ValidationError):  # only from engineering review
            _check("physical_validation", "approved", reason="Met every target", facts=facts)
        self.assertEqual(srules.allowed_targets("approved"), ["engineering_review"])

    def test_draft_closed_and_archived_programs_are_limited(self):
        with self.assertRaises(ValidationError):
            _check("evidence_review", "candidate_assessment", status="draft", facts={"dataset_count": 1})
        for kwargs in ({"status": "completed"}, {"archived": True}):
            with self.assertRaises(ValidationError):
                _check("requirements", "evidence_collection", **kwargs)
        with self.assertRaises(ValidationError):
            _check("approved", "closed")
        self.assertEqual(srules.allowed_targets("closed"), [])

    def test_unknown_same_and_long_reason(self):
        for target in ("nope", "intake"):
            with self.assertRaises(ValidationError):
                _check("intake", target, status="draft")
        with self.assertRaises(ValidationError):
            _check("requirements", "intake", status="draft", reason="x" * 501)


class TestIssueRules(unittest.TestCase):
    def facts(self, **kw):
        base = {"status": "active", "stage": "experiment_planning", "dataset_count": 1, "candidate_count": 2, "unranked_count": 0,
                "stale_ranked_count": 0, "inputs_changed_count": 0, "pending_test_count": 0, "failed_outcome_count": 0, "outcome_count": 0}
        return {**base, **kw}

    def kinds(self, **kw):
        return [i["type"] for i in prules.derive_issues(self.facts(**kw))]

    def test_a_clean_program_has_no_issues(self):
        self.assertEqual(self.kinds(), [])

    def test_each_issue_comes_from_a_record(self):
        self.assertEqual(self.kinds(stale_ranked_count=2), ["ranking_stale"])
        self.assertEqual(self.kinds(inputs_changed_count=1), ["inputs_changed"])
        self.assertEqual(self.kinds(pending_test_count=3), ["test_pending"])
        self.assertEqual(self.kinds(unranked_count=1), ["ranking_needed"])
        self.assertEqual(self.kinds(stage="engineering_review"), ["decision_needed"])
        self.assertEqual(self.kinds(stage="evidence_collection", dataset_count=0), ["evidence_needed"])
        self.assertEqual(self.kinds(stage="candidate_assessment", candidate_count=0), ["candidates_needed"])
        self.assertEqual(self.kinds(failed_outcome_count=1), ["failed_result"])

    def test_pending_tests_only_matter_once_planning_has_started(self):
        self.assertEqual(self.kinds(stage="candidate_assessment", pending_test_count=2), [])

    def test_issues_are_sorted_by_severity(self):
        severities = [i["severity"] for i in prules.derive_issues(self.facts(stage="engineering_review", stale_ranked_count=1, failed_outcome_count=1))]
        self.assertEqual(severities, sorted(severities, key=prules.SEVERITY_ORDER.get))

    def test_drafts_closed_and_decided_programs_have_none(self):
        for kw in ({"status": "draft"}, {"status": "completed", "stage": "closed"}, {"stage": "approved"}, {"stage": "rejected"}):
            self.assertEqual(self.kinds(stale_ranked_count=1, failed_outcome_count=1, **kw), [], kw)

    def test_low_severity_is_not_attention(self):
        self.assertFalse(prules.needs_attention(prules.derive_issues(self.facts(stage="evidence_collection", dataset_count=0))))
        self.assertTrue(prules.needs_attention(prules.derive_issues(self.facts(stale_ranked_count=1))))


class TestProgramStages(lc.LifecycleBase):
    dir_name = "_test_env_program_stages"

    def post_stage(self, client, case_id, stage, reason=None):
        return client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": stage, "reason": reason})

    def start(self, client, case_id):
        r = client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.assertEqual(r.status_code, 200, r.text)

    def record_outcome(self, client, case_id, cand_id, passed):
        r = client.post(f"/api/change-cases/{case_id}/candidates/{cand_id}/outcome", json={"actual_result": "310 h", "passed_spec": passed})
        self.assertIn(r.status_code, (200, 201), r.text)

    def history(self, client, case_id):
        r = client.get(f"/api/change-cases/{case_id}/stage-history")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    # ------------------------------------------------------------ the flow
    def test_a_new_program_starts_at_intake(self):
        _, client, case_id = self.setup_case("Fresh")
        r = client.get(f"/api/change-cases/{case_id}/stage")
        self.assertEqual((r.json()["stage"], r.json()["label"]), ("intake", "Intake"))
        self.assertEqual(self.history(client, case_id), [])

    def test_the_full_path_to_an_approval_with_real_records(self):
        org_id, client, case_id, ids = self.case_with_evidence("Flow", candidates=2, rank=True)
        for stage in ("requirements", "evidence_collection", "evidence_review"):
            self.assertEqual(self.post_stage(client, case_id, stage).status_code, 200)
        self.assertEqual(self.post_stage(client, case_id, "candidate_assessment").status_code, 400)  # not started
        self.start(client, case_id)
        for stage in ("candidate_assessment", "experiment_planning", "physical_validation"):
            self.assertEqual(self.post_stage(client, case_id, stage).status_code, 200, stage)
        self.assertEqual(self.post_stage(client, case_id, "engineering_review").status_code, 400)  # no result yet
        self.record_outcome(client, case_id, ids[0], True)
        self.assertEqual(self.post_stage(client, case_id, "engineering_review").status_code, 200)
        self.assertEqual(self.post_stage(client, case_id, "approved").status_code, 400)  # reason needed
        ok = self.post_stage(client, case_id, "approved", "Met the target in the salt spray test")
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/stage").json()["stage"], "approved")
        steps = [(h["from_stage"], h["to_stage"]) for h in reversed(self.history(client, case_id))]
        self.assertEqual(steps[0], ("intake", "requirements"))
        self.assertEqual(steps[-1], ("engineering_review", "approved"))
        self.assertEqual(len(steps), 8)
        self.assertTrue(all(h["actor"] for h in self.history(client, case_id)))

    def test_the_stage_options_say_why_a_move_is_blocked(self):
        _, client, case_id = self.setup_case("Options")
        for stage in ("requirements", "evidence_collection"):
            self.post_stage(client, case_id, stage)
        opts = {o["stage"]: o for o in client.get(f"/api/change-cases/{case_id}/stage").json()["options"]}
        self.assertIsNone(opts["requirements"]["blocked_reason"])
        self.assertTrue(opts["requirements"]["needs_reason"])
        self.assertIn("Upload evidence", opts["evidence_review"]["blocked_reason"])

    def test_refusals_change_nothing(self):
        org_id, client, case_id = self.setup_case("Refuse")
        for body in ({"to_stage": "approved"}, {"to_stage": "nope"}, {"to_stage": "closed"}, {"to_stage": ""}):
            r = client.post(f"/api/change-cases/{case_id}/stage", json=body)
            self.assertIn(r.status_code, (400, 422), body)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/stage").json()["stage"], "intake")
        self.assertEqual(self.history(client, case_id), [])

    def test_going_back_is_recorded_with_its_reason(self):
        _, client, case_id = self.setup_case("Back")
        self.post_stage(client, case_id, "requirements")
        self.assertEqual(self.post_stage(client, case_id, "intake").status_code, 400)
        self.assertEqual(self.post_stage(client, case_id, "intake", "Wrong template").status_code, 200)
        self.assertEqual(self.history(client, case_id)[0]["reason"], "Wrong template")

    def test_members_cannot_approve_but_owners_can(self):
        org_id, client, case_id, ids = self.case_with_evidence("Role", candidates=1, rank=True)
        for stage in ("requirements", "evidence_collection", "evidence_review"):
            self.post_stage(client, case_id, stage)
        self.start(client, case_id)
        for stage in ("candidate_assessment", "experiment_planning", "physical_validation"):
            self.post_stage(client, case_id, stage)
        self.record_outcome(client, case_id, ids[0], True)
        self.post_stage(client, case_id, "engineering_review")
        member = self.member_client(client, "member")
        r = self.post_stage(member, case_id, "approved", "Met the target")
        self.assertEqual(r.status_code, 400)
        self.assertIn("owner or admin", r.json()["error"])
        self.assertEqual(self.post_stage(client, case_id, "approved", "Met the target").status_code, 200)

    def test_closing_the_program_closes_the_stage_and_freezes_it(self):
        _, client, case_id = self.setup_case("Close")
        self.start(client, case_id)
        self.assertEqual(client.patch(f"/api/change-cases/{case_id}/status", json={"status": "completed"}).status_code, 200)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/stage").json()["stage"], "closed")
        h = self.history(client, case_id)[0]
        self.assertEqual((h["to_stage"], h["reason"]), ("closed", "Program closed"))
        self.assertEqual(self.post_stage(client, case_id, "requirements", "Reopen").status_code, 400)

    def test_an_archived_program_cannot_change_stage(self):
        _, client, case_id = self.setup_case("Arch")
        client.post(f"/api/change-cases/{case_id}/archive")
        self.assertEqual(self.post_stage(client, case_id, "requirements").status_code, 400)

    def test_another_organization_cannot_see_or_change_a_stage(self):
        _, client, case_id = self.setup_case("Mine")
        _, other = self.org_and_client("Theirs")
        for r in (other.get(f"/api/change-cases/{case_id}/stage"), other.get(f"/api/change-cases/{case_id}/stage-history"),
                  self.post_stage(other, case_id, "requirements")):
            self.assertEqual(r.status_code, 404)
        self.assertEqual(client.get(f"/api/change-cases/{case_id}/stage").json()["stage"], "intake")
        anon = self.client_factory()
        self.assertEqual(anon.get(f"/api/change-cases/{case_id}/stage").status_code, 401)

    def test_a_concurrent_change_from_the_same_starting_point_only_applies_once(self):
        from backend.app.repositories import program_stage_repo
        org_id, client, case_id = self.setup_case("Race")
        self.assertTrue(program_stage_repo.set_stage(org_id, case_id, "intake", "requirements", None, None))
        self.assertFalse(program_stage_repo.set_stage(org_id, case_id, "intake", "requirements", None, None))
        self.assertEqual(len(self.history(client, case_id)), 1)

    def test_a_stage_change_is_audited(self):
        from backend.app.config.database import db_connection
        org_id, client, case_id = self.setup_case("Audit")
        self.post_stage(client, case_id, "requirements")
        with db_connection() as conn:
            rows = conn.execute(text("SELECT detail FROM audit_logs WHERE organization_id = :o AND action = 'change_case.stage_change'"),
                                {"o": org_id}).all()
        self.assertEqual(len(rows), 1)
        self.assertIn(f"change_case_id={case_id} from=intake to=requirements", rows[0][0])


class TestOverview(lc.LifecycleBase):
    dir_name = "_test_env_overview"

    def overview(self, client, days=None):
        r = client.get("/api/portfolio/overview" + (f"?days={days}" if days is not None else ""))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def move(self, client, case_id, *stages):
        for s in stages:
            r = client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
            self.assertEqual(r.status_code, 200, (s, r.text))

    def issue_types(self, ov):
        return {a["type"] for a in ov["actions"]}

    def test_an_empty_organization_has_zeros_and_no_invented_rows(self):
        org_id, client = self.org_and_client("Empty")
        ov = self.overview(client)
        self.assertEqual(ov["total_programs"], 0)
        self.assertEqual(ov["kpis"], {"active_programs": 0, "evidence_review": 0, "tests_pending": 0, "needs_attention": 0})
        self.assertEqual((ov["attention"], ov["actions"], ov["decisions"], ov["activity"], ov["issue_types"]), ([], [], [], [], []))
        self.assertEqual(len(ov["pipeline"]), 11)
        self.assertTrue(all(p["count"] == 0 for p in ov["pipeline"]))

    def test_every_number_matches_the_records(self):
        org_id, client, a, ids_a = self.case_with_evidence("A", candidates=2, rank=True)
        draft = self.make_case(client, lc.SPEC)
        started = self.make_case(client, lc.SPEC)
        self.move(client, a, "requirements", "evidence_collection", "evidence_review")
        client.patch(f"/api/change-cases/{a}/status", json={"status": "active"})
        self.move(client, a, "candidate_assessment", "experiment_planning")
        client.patch(f"/api/change-cases/{started}/status", json={"status": "active"})
        ov = self.overview(client)
        self.assertEqual(ov["total_programs"], 3)
        self.assertEqual(ov["kpis"]["active_programs"], 2)
        counts = {p["stage"]: p["count"] for p in ov["pipeline"]}
        self.assertEqual((counts["experiment_planning"], counts["intake"]), (1, 2))
        self.assertEqual(sum(counts.values()), 3)
        self.assertEqual(ov["kpis"]["evidence_review"], 0)
        self.assertEqual(ov["kpis"]["tests_pending"], 2)       # two ranked candidates, no result recorded yet
        self.assertEqual(ov["kpis"]["needs_attention"], 1)
        self.assertEqual(ov["status_summary"], {"no_open_issues": 1, "open_issues": 1, "high_priority": 0})
        self.assertNotIn(draft, [r["id"] for r in ov["attention"]])

    def test_issues_appear_from_real_changes_and_open_their_program(self):
        org_id, client, case_id, ids = self.case_with_evidence("Issues", candidates=2, rank=True)
        self.move(client, case_id, "requirements", "evidence_collection", "evidence_review")
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.move(client, case_id, "candidate_assessment", "experiment_planning")
        ov = self.overview(client)
        self.assertIn("test_pending", self.issue_types(ov))
        self.assertTrue(all(a["program_id"] == case_id for a in ov["actions"]))
        self.assertEqual(ov["kpis"]["tests_pending"], 2)
        self.assertEqual(ov["attention"][0]["id"], case_id)

        # newer evidence makes the ranking stale
        r = self.upload(client, case_id, "more.csv", F.csv_bytes(lc.HEAD, F.base_rows(14)))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIn("ranking_stale", self.issue_types(self.overview(client)))

        # changing a candidate after ranking is its own issue
        cid = ids[0]
        patch = client.patch(f"/api/change-cases/{case_id}/candidates/{cid}", json={"features": {"crosslinker_ratio": 0.2, "cure_temp_c": 160}})
        if patch.status_code == 200:
            self.assertIn("inputs_changed", self.issue_types(self.overview(client)))

        # a failed physical result is high priority and clears that candidate's pending test
        self.move(client, case_id, "physical_validation")
        client.post(f"/api/change-cases/{case_id}/candidates/{ids[1]}/outcome", json={"actual_result": "120 h", "passed_spec": False})
        ov = self.overview(client)
        self.assertIn("failed_result", self.issue_types(ov))
        self.assertEqual(ov["actions"][0]["severity"], "high")
        self.assertEqual(ov["status_summary"]["high_priority"], 1)
        self.assertEqual(ov["kpis"]["tests_pending"], 1)

    def test_a_decision_needed_and_recent_decisions(self):
        org_id, client, case_id, ids = self.case_with_evidence("Decide", candidates=1, rank=True)
        self.move(client, case_id, "requirements", "evidence_collection", "evidence_review")
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.move(client, case_id, "candidate_assessment", "experiment_planning", "physical_validation")
        client.post(f"/api/change-cases/{case_id}/candidates/{ids[0]}/outcome", json={"actual_result": "330 h", "passed_spec": True})
        self.move(client, case_id, "engineering_review")
        self.assertIn("decision_needed", self.issue_types(self.overview(client)))
        r = client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "approved", "reason": "Met the target"})
        self.assertEqual(r.status_code, 200, r.text)
        ov = self.overview(client)
        self.assertEqual(ov["actions"], [])
        self.assertEqual([(d["outcome"], d["reason"], d["program_id"]) for d in ov["decisions"]], [("Approved", "Met the target", case_id)])

    def test_activity_is_real_labelled_and_linked(self):
        org_id, client, case_id, ids = self.case_with_evidence("Act", candidates=1, rank=True)
        ov = self.overview(client)
        labels = [a["label"] for a in ov["activity"]]
        for expected in ("Program created", "Evidence uploaded", "Candidate added", "Candidates ranked"):
            self.assertIn(expected, labels)
        self.assertTrue(all(a["program_id"] == case_id and a["actor"] for a in ov["activity"]))

    def test_the_range_limits_activity_and_decisions(self):
        from backend.app.config.database import db_transaction
        org_id, client, case_id = self.setup_case("Range")
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        with db_transaction() as conn:
            conn.execute(text("UPDATE audit_logs SET created_at = :o WHERE organization_id = :org"), {"o": old, "org": org_id})
        self.assertEqual(self.overview(client, 7)["activity"], [])
        self.assertEqual(self.overview(client, 30)["activity"], [])
        self.assertGreater(len(self.overview(client, 90)["activity"]), 0)
        self.assertGreater(len(self.overview(client, 0)["activity"]), 0)
        self.assertEqual(client.get("/api/portfolio/overview?days=45").status_code, 400)
        self.assertEqual(client.get("/api/portfolio/overview?days=abc").status_code, 422)

    def test_a_removed_program_leaves_activity_without_a_link(self):
        org_id, client, case_id = self.setup_case("Gone")
        self.assertEqual(client.delete(f"/api/change-cases/{case_id}").status_code, 200)
        for a in self.overview(client)["activity"]:
            self.assertIsNone(a["program_id"], a)

    def test_organizations_never_see_each_other(self):
        _, mine, a = self.setup_case("TenantA")
        _, theirs = self.org_and_client("TenantB")
        mine.patch(f"/api/change-cases/{a}/status", json={"status": "active"})
        mine.post(f"/api/change-cases/{a}/stage", json={"to_stage": "requirements"})
        ov = self.overview(theirs)
        self.assertEqual((ov["total_programs"], ov["activity"], ov["actions"]), (0, [], []))
        self.assertEqual(theirs.get("/api/portfolio/programs").json()["total"], 0)
        self.assertEqual(self.overview(mine)["total_programs"], 1)

    def test_the_program_list_filters(self):
        org_id, client, case_id, ids = self.case_with_evidence("Filter", candidates=1, rank=True)
        other = self.make_case(client, lc.SPEC)
        self.move(client, case_id, "requirements", "evidence_collection", "evidence_review")
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        self.move(client, case_id, "candidate_assessment", "experiment_planning")
        def listing(query):
            r = client.get("/api/portfolio/programs" + query)
            self.assertEqual(r.status_code, 200, r.text)
            return [p["id"] for p in r.json()["programs"]]
        self.assertEqual(sorted(listing("")), sorted([case_id, other]))
        self.assertEqual(listing("?filter=active"), [case_id])
        self.assertEqual(listing("?filter=tests_pending"), [case_id])
        self.assertEqual(listing("?filter=attention"), [case_id])
        self.assertEqual(listing("?stage=experiment_planning"), [case_id])
        self.assertEqual(listing("?stage=intake"), [other])
        self.assertEqual(sorted(listing("?stage=intake,experiment_planning")), sorted([case_id, other]))
        self.assertEqual(listing("?issue=test_pending"), [case_id])
        self.assertEqual(sorted(listing("?q=CASE")), sorted([case_id, other]))
        self.assertEqual(listing("?q=zzz"), [])
        for bad in ("?filter=nope", "?stage=nope"):
            self.assertEqual(client.get("/api/portfolio/programs" + bad).status_code, 400)

    def test_archived_programs_are_left_out(self):
        org_id, client, case_id = self.setup_case("Archived")
        client.post(f"/api/change-cases/{case_id}/archive")
        self.assertEqual(self.overview(client)["total_programs"], 0)

    def test_it_needs_a_session(self):
        anon = self.client_factory()
        self.assertEqual(anon.get("/api/portfolio/overview").status_code, 401)
        self.assertEqual(anon.get("/api/portfolio/programs").status_code, 401)

    def test_any_member_can_read_it(self):
        org_id, client, case_id = self.setup_case("Member")
        member = self.member_client(client, "member")
        self.assertEqual(self.overview(member)["total_programs"], 1)


# ---------------------------------------------------------------- migration (SQLite and PostgreSQL)
BEFORE_STAGE = "f6c9d4e2a1b3"
STAGE_REVISION = "a7e3b9c1d5f2"


class _StageMigrationScenarios:
    url = None

    def new_engine(self):
        raise NotImplementedError

    def _seed(self, engine):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('Mig', 't')"))
            org = conn.execute(text("SELECT id FROM organizations")).scalar()
            conn.execute(text("INSERT INTO users (email, password_hash, display_name, is_active, created_at) VALUES ('m@example.com', 'h', 'M', 1, 't')"))
            user = conn.execute(text("SELECT id FROM users")).scalar()
            ids = {}

            def case(name, status):
                conn.execute(text("""INSERT INTO change_cases (organization_id, created_by_user_id, name, trigger_type, restricted_substance,
                                     qualification_spec_json, status, created_at, updated_at)
                                     VALUES (:o, :u, :n, 'other', 'x', '{}', :s, '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00')"""),
                             {"o": org, "u": user, "n": name, "s": status})
                ids[name] = conn.execute(text("SELECT id FROM change_cases WHERE name = :n"), {"n": name}).scalar()

            def dataset(name):
                conn.execute(text("""INSERT INTO qualification_datasets (organization_id, change_case_id, uploaded_by_user_id, original_filename,
                                     stored_filename, row_count, created_at) VALUES (:o, :c, :u, 'f.csv', 's.csv', 5, 't')"""),
                             {"o": org, "c": ids[name], "u": user})
                return conn.execute(text("SELECT MAX(id) FROM qualification_datasets")).scalar()

            def candidate(name):
                conn.execute(text("""INSERT INTO candidate_substitutes (organization_id, change_case_id, candidate_name, properties_json, created_at)
                                     VALUES (:o, :c, 'cand', '{}', 't')"""), {"o": org, "c": ids[name]})
                return conn.execute(text("SELECT MAX(id) FROM candidate_substitutes")).scalar()

            for name, status in (("draft", "draft"), ("done", "completed"), ("active_empty", "active"), ("with_data", "active"),
                                 ("with_cands", "active"), ("ranked", "active"), ("tested", "active")):
                case(name, status)
            ds = dataset("with_data")
            dataset("with_cands"); candidate("with_cands")
            d = dataset("ranked"); cand = candidate("ranked")
            conn.execute(text("""INSERT INTO predictions (organization_id, candidate_id, dataset_version_id, model_version, predicted_probability,
                                 uncertainty_std, created_at) VALUES (:o, :c, :d, 'm', 0.5, 0.1, 't')"""), {"o": org, "c": cand, "d": d})
            d2 = dataset("tested"); cand2 = candidate("tested")
            conn.execute(text("""INSERT INTO qualification_outcomes (organization_id, candidate_id, actual_result, passed_spec, recorded_by_user_id, created_at)
                                 VALUES (:o, :c, '1', :p, :u, 't')"""), {"o": org, "c": cand2, "p": 1, "u": user})
        return ids

    def test_upgrade_gives_each_program_the_stage_its_records_support(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), BEFORE_STAGE)
                ids = self._seed(engine)
                command.upgrade(_config(), STAGE_REVISION)
                with engine.connect() as conn:
                    stage = {r[0]: r[1] for r in conn.execute(text("SELECT name, stage FROM change_cases"))}
                    history = conn.execute(text("SELECT COUNT(*) FROM program_stage_history")).scalar()
                    reasons = {r[0] for r in conn.execute(text("SELECT reason FROM program_stage_history"))}
                    version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                self.assertEqual(stage, {"draft": "intake", "done": "closed", "active_empty": "evidence_collection", "with_data": "evidence_review",
                                         "with_cands": "candidate_assessment", "ranked": "experiment_planning", "tested": "physical_validation"})
                self.assertEqual(history, 6)           # every program not at intake
                self.assertEqual(reasons, {"Set from existing records"})
                self.assertEqual(version, STAGE_REVISION)
                self.assertTrue({"stage", "stage_changed_at"} <= {c["name"] for c in inspect(engine).get_columns("change_cases")})
        finally:
            engine.dispose()

    def test_downgrade_removes_only_the_new_pieces_and_upgrade_repeats(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), BEFORE_STAGE)
                self._seed(engine)
                command.upgrade(_config(), "head")
                command.downgrade(_config(), BEFORE_STAGE)
                insp = inspect(engine)
                self.assertNotIn("program_stage_history", insp.get_table_names())
                self.assertFalse({"stage", "stage_changed_at"} & {c["name"] for c in insp.get_columns("change_cases")})
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM change_cases")).scalar(), 7)
                command.upgrade(_config(), "head")
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT stage FROM change_cases WHERE name = 'done'")).scalar(), "closed")
        finally:
            engine.dispose()

    def test_deleting_a_program_removes_its_history(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), BEFORE_STAGE)
                ids = self._seed(engine)
                command.upgrade(_config(), "head")
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM change_cases WHERE name = 'draft' OR name = 'done'"))
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM program_stage_history h WHERE NOT EXISTS "
                                                       "(SELECT 1 FROM change_cases c WHERE c.id = h.change_case_id)")).scalar(), 0)
        finally:
            engine.dispose()


class TestSqliteStageMigration(_StageMigrationScenarios, unittest.TestCase):
    def setUp(self):
        import shutil
        self.dir = os.path.join(os.path.dirname(__file__), f"_test_env_stage_mig_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.dir)
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.url = f"sqlite:///{self.dir}/m.db"

    def new_engine(self):
        engine = create_engine(self.url)

        @event.listens_for(engine, "connect")
        def _fk(dbapi_connection, record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return engine


@unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")
class TestPostgresStageMigration(_StageMigrationScenarios, unittest.TestCase):
    def setUp(self):
        self.schema = f"stagemig_{uuid.uuid4().hex[:10]}"
        self.admin = create_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.url = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={self.schema}"}).render_as_string(hide_password=False)

    def tearDown(self):
        with self.admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        self.admin.dispose()

    def new_engine(self):
        return create_engine(self.url)


@unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")
class TestProgramStagesPostgres(TestProgramStages):
    """The API scenarios above on a real PostgreSQL schema built by the migrations."""
    dir_name = "_test_env_program_stages_pg"
    setUpClass = classmethod(lambda cls: lc.TestDeletionPostgres.setUpClass.__func__(cls))
    tearDownClass = classmethod(lambda cls: lc.TestDeletionPostgres.tearDownClass.__func__(cls))


@unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")
class TestOverviewPostgres(TestOverview):
    dir_name = "_test_env_overview_pg"
    setUpClass = classmethod(lambda cls: lc.TestDeletionPostgres.setUpClass.__func__(cls))
    tearDownClass = classmethod(lambda cls: lc.TestDeletionPostgres.tearDownClass.__func__(cls))


if __name__ == "__main__":
    unittest.main()
