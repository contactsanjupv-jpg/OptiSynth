"""Level 2: organization profile, sign-up policy, password and sessions, audit history, data export, notifications.
Run:  python3 -m unittest backend.tests.test_administration -v
(PostgreSQL scenarios: set OPTISYNTH_TEST_POSTGRES_URL as in test_migration_portability.)"""
import json
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

from alembic import command
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url

from backend.app.config import settings as settings_module
from backend.app.config.settings import settings
from backend.app.security import login_throttle
from backend.app.security.sessions import read_session_token
from backend.tests import evidence_fixtures as F
from backend.tests import test_diagnostic_lifecycle as lc
from backend.tests.test_migration_portability import POSTGRES_URL, _config, _UseDatabase

PASSWORD = "correct-horse-battery-staple"
NEW_PASSWORD = "another-long-passphrase-2026"


class Base(lc.LifecycleBase):
    dir_name = "_test_env_administration"

    def setUp(self):
        super().setUp()
        login_throttle.throttle.reset()

    def me(self, client):
        r = client.get("/api/auth/me")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def login(self, email, password=PASSWORD):
        client = self.client_factory()
        r = client.post("/api/auth/login", json={"email": email, "password": password})
        return client, r

    def token(self, client):
        return client.cookies.get(settings.SESSION_COOKIE_NAME)

    def replay(self, token):
        return self.client_factory().get("/api/auth/me", headers={"Cookie": f"{settings.SESSION_COOKIE_NAME}={token}"})

    def sql(self, statement, **params):
        from backend.app.config.database import db_transaction
        with db_transaction() as conn:
            return conn.execute(text(statement), params)

    def rows(self, statement, **params):
        from backend.app.config.database import db_connection
        with db_connection() as conn:
            return [dict(r) for r in conn.execute(text(statement), params).mappings()]


class TestSignupPolicy(Base):
    dir_name = "_test_env_signup_policy"

    def test_the_default_follows_the_environment(self):
        for env, expected in (("production", False), ("development", True)):
            with mock.patch.dict(os.environ, {"ENV": env, "SECRET_KEY": "k" * 40, "PASSWORD_PEPPER": "p" * 40,
                                              "SESSION_COOKIE_SECURE": "true", "DATABASE_URL": "sqlite:///x.db",
                                              "CORS_ALLOWED_ORIGIN": "http://localhost:3000"}, clear=False):
                os.environ.pop("ALLOW_PUBLIC_SIGNUP", None)
                self.assertEqual(settings_module.Settings().ALLOW_PUBLIC_SIGNUP, expected, env)
        with mock.patch.dict(os.environ, {"ALLOW_PUBLIC_SIGNUP": "true"}):
            self.assertTrue(settings_module.Settings().ALLOW_PUBLIC_SIGNUP)

    def test_without_public_sign_up_a_new_organization_is_refused(self):
        with mock.patch.object(settings, "ALLOW_PUBLIC_SIGNUP", False):
            c = self.client_factory()
            self.assertEqual(c.get("/api/auth/config").json(), {"public_signup": False})
            r = c.post("/api/auth/signup", json={"email": "new@example.com", "password": PASSWORD, "organization_name": "Nope"})
            self.assertEqual(r.status_code, 403, r.text)
            self.assertIn("invitation only", r.json()["error"])
            self.assertEqual(self.rows("SELECT COUNT(*) AS n FROM users WHERE email = 'new@example.com'")[0]["n"], 0)

    def test_an_invitation_still_works_and_the_config_reports_the_setting(self):
        org_id, owner = self.org_and_client("Invite")
        with mock.patch.object(settings, "ALLOW_PUBLIC_SIGNUP", False):
            member = self.member_client(owner, "member")
            self.assertEqual(self.me(member)["role"], "member")
            self.assertEqual(self.client_factory().get("/api/auth/config").json(), {"public_signup": False})
        self.assertEqual(self.client_factory().get("/api/auth/config").json(), {"public_signup": True})

    def test_the_operator_script_creates_an_organization_and_owner(self):
        import subprocess, sys
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env = {**os.environ, "SECRET_KEY": settings.SECRET_KEY, "PASSWORD_PEPPER": settings.PASSWORD_PEPPER,
               "DATABASE_URL": settings.DATABASE_URL, "OPTISYNTH_OWNER_PASSWORD": PASSWORD, "PYTHONPATH": root}
        done = subprocess.run([sys.executable, "scripts/create_organization.py", "--organization", "Script Co", "--email", "owner@script.example.com",
                               "--name", "Owner"], capture_output=True, text=True, cwd=root, env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        self.assertIn("Created organization", done.stdout)
        client, r = self.login("owner@script.example.com")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.me(client)["role"], "owner")
        again = subprocess.run([sys.executable, "scripts/create_organization.py", "--organization", "Script Co", "--email", "owner@script.example.com"],
                               capture_output=True, text=True, cwd=root, env=env, timeout=120)
        self.assertNotEqual(again.returncode, 0)


class TestOrganizationProfile(Base):
    dir_name = "_test_env_org_profile"

    def test_everyone_can_read_and_it_starts_empty(self):
        org_id, owner = self.org_and_client("Read")
        member = self.member_client(owner, "member")
        for c in (owner, member):
            body = c.get("/api/organization").json()
            self.assertEqual((body["id"], body["industry"], body["website"]), (org_id, None, None))
            self.assertIn("Specialty chemicals", body["industries"])

    def test_owner_and_admin_can_change_it_and_it_is_audited(self):
        org_id, owner = self.org_and_client("Edit")
        admin = self.member_client(owner, "admin")
        r = owner.patch("/api/organization", json={"name": "  Acme Materials ", "industry": "Coatings and paints", "website": "https://acme.example.com"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["name"], r.json()["website"]), ("Acme Materials", "https://acme.example.com"))
        self.assertEqual(admin.patch("/api/organization", json={"name": "Acme Materials", "industry": "Other"}).status_code, 200)
        events = self.rows("SELECT detail FROM audit_logs WHERE organization_id = :o AND action = 'organization.update' ORDER BY id", o=org_id)
        self.assertEqual(len(events), 2)
        self.assertIn("name", events[0]["detail"])
        self.assertEqual(admin.patch("/api/organization", json={"name": "Acme Materials", "industry": "Other"}).status_code, 200)
        self.assertEqual(len(self.rows("SELECT id FROM audit_logs WHERE organization_id = :o AND action = 'organization.update'", o=org_id)), 2)  # no change, no event

    def test_a_member_cannot_change_it(self):
        org_id, owner = self.org_and_client("Member")
        member = self.member_client(owner, "member")
        r = member.patch("/api/organization", json={"name": "Hijacked"})
        self.assertEqual(r.status_code, 403)
        self.assertNotEqual(owner.get("/api/organization").json()["name"], "Hijacked")

    def test_invalid_values_are_refused_with_the_field(self):
        org_id, owner = self.org_and_client("Invalid")
        for body, field in (({"name": "   "}, "name"), ({"name": "x" * 121}, "name"), ({"name": "A", "website": "ftp://x"}, "website"),
                            ({"name": "A", "website": "not a url"}, "website"), ({"name": "A", "industry": "x" * 81}, "industry")):
            r = owner.patch("/api/organization", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertEqual(r.json().get("field"), field)

    def test_organizations_are_separate(self):
        a, owner_a = self.org_and_client("TenantA")
        b, owner_b = self.org_and_client("TenantB")
        owner_a.patch("/api/organization", json={"name": "Only A"})
        self.assertNotEqual(owner_b.get("/api/organization").json()["name"], "Only A")
        self.assertEqual(self.client_factory().get("/api/organization").status_code, 401)


class TestPasswordAndSessions(Base):
    dir_name = "_test_env_password_sessions"

    def setUpFounder(self, label):
        org_id, client = self.org_and_client(label)
        return org_id, client, self.me(client)["user"]["email"]

    def test_display_name_can_be_changed(self):
        org_id, client, email = self.setUpFounder("Name")
        r = client.patch("/api/auth/profile", json={"display_name": "  Ada Lovelace "})
        self.assertEqual(r.json()["user"]["display_name"], "Ada Lovelace")
        self.assertEqual(client.patch("/api/auth/profile", json={"display_name": "x" * 201}).status_code, 422)
        self.assertIsNone(client.patch("/api/auth/profile", json={"display_name": "  "}).json()["user"]["display_name"])

    def test_changing_the_password_needs_the_current_one_and_ends_other_sessions(self):
        org_id, a, email = self.setUpFounder("Password")
        b, _ = self.login(email)
        token_b = self.token(b)
        r = a.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["other_sessions_ended"], 1)
        self.assertEqual(a.get("/api/auth/me").status_code, 200)      # this session continues
        self.assertEqual(self.replay(token_b).status_code, 401)        # the other one ended
        self.assertEqual(self.login(email, PASSWORD)[1].status_code, 400)
        self.assertEqual(self.login(email, NEW_PASSWORD)[1].status_code, 200)
        self.assertEqual(len(self.rows("SELECT id FROM audit_logs WHERE action = 'auth.password_change' AND organization_id = :o", o=org_id)), 1)

    def test_wrong_current_password_short_new_password_and_reuse_are_refused(self):
        org_id, a, email = self.setUpFounder("Refuse")
        r = a.post("/api/auth/password", json={"current_password": "wrong-password-xx", "new_password": NEW_PASSWORD})
        self.assertEqual((r.status_code, r.json()["field"]), (400, "current_password"))
        self.assertEqual(a.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"}).status_code, 422)
        r = a.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": PASSWORD})
        self.assertEqual((r.status_code, r.json()["field"]), (400, "new_password"))
        self.assertEqual(self.login(email, PASSWORD)[1].status_code, 200)   # nothing changed

    def test_guessing_the_current_password_is_limited(self):
        org_id, a, email = self.setUpFounder("Guess")
        for _ in range(5):
            self.assertEqual(a.post("/api/auth/password", json={"current_password": "wrong-password-xx", "new_password": NEW_PASSWORD}).status_code, 400)
        r = a.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        self.assertEqual(r.status_code, 429)
        self.assertIn("Retry-After", r.headers)

    def test_the_session_list_marks_the_current_one_and_hides_other_peoples(self):
        org_id, a, email = self.setUpFounder("List")
        b, _ = self.login(email)
        other_org, other = self.org_and_client("Other")
        rows = a.get("/api/auth/sessions").json()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["current"] for r in rows].count(True), 1)
        self.assertTrue(all(set(r) == {"id", "device", "address", "created_at", "expires_at", "current"} for r in rows))  # no session secrets
        self.assertEqual(len(other.get("/api/auth/sessions").json()), 1)

    def test_one_session_can_be_ended_and_its_cookie_stops_working(self):
        org_id, a, email = self.setUpFounder("End")
        b, _ = self.login(email)
        token_b = self.token(b)
        target = next(r for r in a.get("/api/auth/sessions").json() if not r["current"])
        self.assertEqual(a.delete(f"/api/auth/sessions/{target['id']}").status_code, 200)
        self.assertEqual(self.replay(token_b).status_code, 401)
        self.assertEqual(a.get("/api/auth/me").status_code, 200)
        self.assertEqual(a.delete(f"/api/auth/sessions/{target['id']}").status_code, 404)   # already ended

    def test_a_session_of_another_user_cannot_be_ended(self):
        org_id, a, _ = self.setUpFounder("Mine")
        other_org, b = self.org_and_client("Theirs")
        theirs = b.get("/api/auth/sessions").json()[0]["id"]
        self.assertEqual(a.delete(f"/api/auth/sessions/{theirs}").status_code, 404)
        self.assertEqual(b.get("/api/auth/me").status_code, 200)

    def test_end_all_other_sessions_and_sign_out_everywhere(self):
        org_id, a, email = self.setUpFounder("Everywhere")
        b, _ = self.login(email)
        c, _ = self.login(email)
        tokens = [self.token(b), self.token(c)]
        self.assertEqual(a.post("/api/auth/sessions/revoke-others").json()["ended"], 2)
        self.assertEqual([self.replay(t).status_code for t in tokens], [401, 401])
        self.assertEqual(a.get("/api/auth/me").status_code, 200)
        d, _ = self.login(email)
        self.assertEqual(a.post("/api/auth/logout-all").json()["ended"], 2)
        self.assertEqual((a.get("/api/auth/me").status_code, d.get("/api/auth/me").status_code), (401, 401))

    def test_ending_the_current_session_signs_this_browser_out(self):
        org_id, a, email = self.setUpFounder("Self")
        current = next(r for r in a.get("/api/auth/sessions").json() if r["current"])
        self.assertEqual(a.delete(f"/api/auth/sessions/{current['id']}").status_code, 200)
        self.assertEqual(a.get("/api/auth/me").status_code, 401)

    def test_security_events_are_your_own(self):
        org_id, a, email = self.setUpFounder("Events")
        self.login(email)
        a.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        labels = [e["label"] for e in a.get("/api/security/events").json()]
        for expected in ("Account created", "Signed in", "Password changed"):
            self.assertIn(expected, labels)
        other_org, other = self.org_and_client("Quiet")
        self.assertNotIn("Password changed", [e["label"] for e in other.get("/api/security/events").json()])

    def test_sessions_record_the_device(self):
        org_id, a, email = self.setUpFounder("Device")
        client = self.client_factory()
        client.post("/api/auth/login", json={"email": email, "password": PASSWORD},
                    headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit Chrome/120 Safari/537"})
        devices = [r["device"] for r in a.get("/api/auth/sessions").json()]
        self.assertIn("Chrome on Mac OS", devices)


class TestAuditHistory(Base):
    dir_name = "_test_env_audit_history"

    def test_only_owners_and_admins_can_read_it(self):
        org_id, owner = self.org_and_client("Who")
        admin, member = self.member_client(owner, "admin"), self.member_client(owner, "member")
        self.assertEqual(owner.get("/api/audit/events").status_code, 200)
        self.assertEqual(admin.get("/api/audit/events").status_code, 200)
        self.assertEqual(member.get("/api/audit/events").status_code, 403)
        self.assertEqual(self.client_factory().get("/api/audit/events").status_code, 401)

    def test_events_are_labelled_and_linked(self):
        org_id, owner, case_id, ids = self.case_with_evidence("Linked", candidates=1, rank=True)
        body = owner.get("/api/audit/events").json()
        labels = {e["label"] for e in body["events"]}
        self.assertTrue({"Program created", "Evidence uploaded", "Candidates ranked"} <= labels)
        self.assertTrue(any(e["program_id"] == case_id and e["program_name"] for e in body["events"]))
        self.assertTrue(all(e["actor"] or e["action"].startswith("operator_") for e in body["events"]))  # only operator actions have no person

    def test_filters_by_person_action_text_and_dates(self):
        org_id, owner = self.org_and_client("Filters")
        member = self.member_client(owner, "member")
        member_id = self.me(member)["user"]["id"]
        owner_id = self.me(owner)["user"]["id"]
        self.make_case(owner, lc.SPEC)
        ev = lambda q: owner.get("/api/audit/events" + q).json()
        self.assertTrue(all(e["actor_id"] == member_id for e in ev(f"?user_id={member_id}")["events"]))
        self.assertTrue(all(e["action"] == "change_case.create" for e in ev("?action=change_case.create")["events"]))
        self.assertGreaterEqual(ev("?q=change_case.create")["total"], 1)
        self.assertEqual(ev("?q=zzzz-no-match")["events"], [])
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.assertGreater(ev(f"?date_from={today}&date_to={today}")["total"], 0)
        self.assertEqual(ev("?date_to=2001-01-01")["events"], [])
        self.assertEqual(owner.get("/api/audit/events?date_from=yesterday").status_code, 400)
        self.assertTrue({a["action"] for a in ev("")["actions"]} >= {"change_case.create"})
        self.assertIn(owner_id, [x["id"] for x in ev("")["actors"]])

    def test_paging(self):
        org_id, owner = self.org_and_client("Paging")
        for _ in range(4):
            self.make_case(owner, lc.SPEC)
        page1 = owner.get("/api/audit/events?limit=2&offset=0").json()
        page2 = owner.get("/api/audit/events?limit=2&offset=2").json()
        self.assertEqual((len(page1["events"]), page1["limit"]), (2, 2))
        self.assertFalse({e["id"] for e in page1["events"]} & {e["id"] for e in page2["events"]})
        self.assertEqual(owner.get("/api/audit/events?limit=9999").json()["limit"], 200)

    def test_one_organization_never_sees_anothers_events(self):
        a, owner_a = self.org_and_client("AuditA")
        b, owner_b = self.org_and_client("AuditB")
        self.make_case(owner_a, lc.SPEC)
        ids_b = {e["id"] for e in owner_b.get("/api/audit/events").json()["events"]}
        ids_a = {e["id"] for e in owner_a.get("/api/audit/events").json()["events"]}
        self.assertFalse(ids_a & ids_b)


class TestDataManagement(Base):
    dir_name = "_test_env_data_management"

    def test_the_upload_inventory_lists_files_with_sizes_for_admins_only(self):
        org_id, owner, case_id, ids = self.case_with_evidence("Inventory", candidates=1, rank=False)
        member = self.member_client(owner, "member")
        self.assertEqual(member.get("/api/data/uploads").status_code, 403)
        body = owner.get("/api/data/uploads").json()
        self.assertGreaterEqual(len(body["files"]), 1)
        f = body["files"][0]
        self.assertEqual((f["program_id"], f["uploaded_by"] is not None), (case_id, True))
        self.assertGreater(f["size_bytes"], 0)
        self.assertEqual(body["total_bytes"], sum(x["size_bytes"] or 0 for x in body["files"]))
        self.assertNotIn("stored_filename", json.dumps(body))

    def test_only_the_owner_can_export_and_the_password_is_asked_again(self):
        org_id, owner, case_id, ids = self.case_with_evidence("Export", candidates=1, rank=True)
        admin = self.member_client(owner, "admin")
        self.assertEqual(admin.post("/api/data/export", json={"password": PASSWORD}).status_code, 403)
        wrong = owner.post("/api/data/export", json={"password": "wrong-password-xx"})
        self.assertEqual((wrong.status_code, wrong.json()["field"]), (400, "password"))
        self.assertEqual(self.rows("SELECT id FROM audit_logs WHERE action = 'data.export' AND organization_id = :o", o=org_id), [])
        ok = owner.post("/api/data/export", json={"password": PASSWORD})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn("attachment", ok.headers["Content-Disposition"])
        data = ok.json()
        self.assertEqual(data["organization"]["id"], org_id)
        self.assertTrue(any(c["id"] == case_id for c in data["change_cases"]))
        self.assertTrue(data["candidate_substitutes"] and data["predictions"] and data["qualification_datasets"])
        self.assertEqual(len(self.rows("SELECT id FROM audit_logs WHERE action = 'data.export' AND organization_id = :o", o=org_id)), 1)

    def test_an_export_never_holds_secrets_or_another_organizations_data(self):
        org_id, owner, case_id, ids = self.case_with_evidence("Secrets", candidates=1, rank=False)
        other_org, other = self.org_and_client("Neighbour")
        other_case = self.make_case(other, lc.SPEC)
        owner.post("/api/team/invitations", json={"email": "pending@example.com", "role": "member"})
        text_out = owner.post("/api/data/export", json={"password": PASSWORD}).text
        for forbidden in ("password_hash", "\"token\"", "stored_filename", "session_id", "invite_url"):
            self.assertNotIn(forbidden, text_out)
        data = json.loads(text_out)
        self.assertNotIn(other_case, [c["id"] for c in data["change_cases"]])
        self.assertNotIn("Neighbour", json.dumps(data["organization"]))
        self.assertTrue(all(m["email"] for m in data["members"]))

    def test_guessing_the_export_password_is_limited(self):
        org_id, owner = self.org_and_client("ExportGuess")
        for _ in range(5):
            self.assertEqual(owner.post("/api/data/export", json={"password": "wrong-password-xx"}).status_code, 400)
        self.assertEqual(owner.post("/api/data/export", json={"password": PASSWORD}).status_code, 429)


class TestNotifications(Base):
    dir_name = "_test_env_notifications"

    def reach_engineering_review(self, owner, label, rank=True):
        org_id, client, case_id, ids = self.case_with_evidence(label, candidates=1, rank=rank, client=owner) if False else (None,) * 4
        return org_id, client, case_id, ids

    def run_to(self, client, case_id, ids, last):
        steps = ["requirements", "evidence_collection", "evidence_review"]
        for s in steps:
            self.assertEqual(client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s}).status_code, 200, s)
        client.patch(f"/api/change-cases/{case_id}/status", json={"status": "active"})
        for s in ("candidate_assessment", "experiment_planning", "physical_validation"):
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": s})
        client.post(f"/api/change-cases/{case_id}/candidates/{ids[0]}/outcome", json={"actual_result": "330 h", "passed_spec": True})
        if last == "engineering_review":
            client.post(f"/api/change-cases/{case_id}/stage", json={"to_stage": "engineering_review"})

    def test_a_stage_change_tells_the_other_owners_and_admins_but_not_the_actor(self):
        org_id, owner, case_id, ids = self.case_with_evidence("Notify", candidates=1, rank=True)
        admin = self.member_client(owner, "admin")
        member = self.member_client(owner, "member")
        self.run_to(owner, case_id, ids, "engineering_review")
        inbox = admin.get("/api/notifications").json()
        titles = [n["title"] for n in inbox["items"]]
        self.assertIn("Engineering decision needed", titles)
        self.assertIn("Evidence ready for review", titles)
        self.assertEqual(inbox["unread"], len(inbox["items"]))
        item = next(n for n in inbox["items"] if n["title"] == "Engineering decision needed")
        self.assertEqual(item["link"], f"/programs/{case_id}")
        self.assertEqual(member.get("/api/notifications").json()["items"], [])
        self.assertNotIn("Engineering decision needed", [n["title"] for n in owner.get("/api/notifications").json()["items"]])

    def test_read_state_and_isolation(self):
        org_id, owner, case_id, ids = self.case_with_evidence("ReadState", candidates=1, rank=True)
        admin = self.member_client(owner, "admin")
        self.run_to(owner, case_id, ids, "engineering_review")
        inbox = admin.get("/api/notifications").json()
        first = inbox["items"][0]["id"]
        self.assertEqual(admin.post(f"/api/notifications/{first}/read").status_code, 200)
        self.assertEqual(admin.get("/api/notifications").json()["unread"], inbox["unread"] - 1)
        self.assertEqual(admin.post(f"/api/notifications/{first}/read").status_code, 404)   # already read
        self.assertEqual(len(admin.get("/api/notifications?unread_only=true").json()["items"]), inbox["unread"] - 1)
        other_org, other = self.org_and_client("Elsewhere")
        self.assertEqual(other.post(f"/api/notifications/{inbox['items'][1]['id']}/read").status_code, 404)
        self.assertEqual(admin.post("/api/notifications/read-all").json()["marked"], inbox["unread"] - 1)
        self.assertEqual(admin.get("/api/notifications").json()["unread"], 0)

    def test_a_new_member_notifies_the_owner(self):
        org_id, owner = self.org_and_client("Joined")
        self.member_client(owner, "member")
        titles = [n["title"] for n in owner.get("/api/notifications").json()["items"]]
        self.assertEqual(titles, ["New team member"])

    def test_it_needs_a_session(self):
        anon = self.client_factory()
        for r in (anon.get("/api/notifications"), anon.post("/api/notifications/read-all"), anon.post("/api/notifications/1/read")):
            self.assertEqual(r.status_code, 401)


# ---------------------------------------------------------------- migration (SQLite and PostgreSQL)
BEFORE_ADMIN = "a7e3b9c1d5f2"
ADMIN_REVISION = "b8f4c2d6e1a9"


class _AdminMigrationScenarios:
    url = None

    def new_engine(self):
        raise NotImplementedError

    def test_upgrade_adds_the_pieces_and_keeps_data_then_downgrade_removes_them(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), BEFORE_ADMIN)
                with engine.begin() as conn:
                    conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('Keep', 't')"))
                command.upgrade(_config(), "head")
                insp = inspect(engine)
                self.assertTrue({"industry", "website", "updated_at"} <= {c["name"] for c in insp.get_columns("organizations")})
                self.assertTrue({"user_agent", "ip_address"} <= {c["name"] for c in insp.get_columns("user_sessions")})
                self.assertIn("notifications", insp.get_table_names())
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT name FROM organizations")).scalar(), "Keep")
                command.downgrade(_config(), BEFORE_ADMIN)
                insp = inspect(engine)
                self.assertNotIn("notifications", insp.get_table_names())
                self.assertFalse({"industry", "website", "updated_at"} & {c["name"] for c in insp.get_columns("organizations")})
                self.assertFalse({"user_agent", "ip_address"} & {c["name"] for c in insp.get_columns("user_sessions")})
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT name FROM organizations")).scalar(), "Keep")
                command.upgrade(_config(), "head")
                self.assertIn("notifications", inspect(engine).get_table_names())
        finally:
            engine.dispose()

    def test_notifications_follow_their_user_and_organization(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), "head")
                with engine.begin() as conn:
                    conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('N', 't')"))
                    org = conn.execute(text("SELECT id FROM organizations")).scalar()
                    conn.execute(text("INSERT INTO users (email, password_hash, display_name, is_active, created_at) VALUES ('n@example.com', 'h', 'N', 1, 't')"))
                    user = conn.execute(text("SELECT id FROM users")).scalar()
                    conn.execute(text("INSERT INTO notifications (organization_id, user_id, kind, title, created_at) VALUES (:o, :u, 'k', 'T', 't')"), {"o": org, "u": user})
                with engine.begin() as conn:
                    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM notifications")).scalar(), 0)
        finally:
            engine.dispose()


class TestSqliteAdminMigration(_AdminMigrationScenarios, unittest.TestCase):
    def setUp(self):
        import shutil
        self.dir = os.path.join(os.path.dirname(__file__), f"_test_env_admin_mig_{uuid.uuid4().hex[:8]}")
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
class TestPostgresAdminMigration(_AdminMigrationScenarios, unittest.TestCase):
    def setUp(self):
        self.schema = f"adminmig_{uuid.uuid4().hex[:10]}"
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


def _pg(cls):
    """A PostgreSQL copy of an API test class, built on a private schema by the migrations."""
    name = cls.__name__ + "Postgres"
    return unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")(type(name, (cls,), {
        "dir_name": cls.dir_name + "_pg",
        "setUpClass": classmethod(lambda c: lc.TestDeletionPostgres.setUpClass.__func__(c)),
        "tearDownClass": classmethod(lambda c: lc.TestDeletionPostgres.tearDownClass.__func__(c)),
    }))


TestSignupPolicyPostgres = _pg(TestSignupPolicy)
TestOrganizationProfilePostgres = _pg(TestOrganizationProfile)
TestPasswordAndSessionsPostgres = _pg(TestPasswordAndSessions)
TestAuditHistoryPostgres = _pg(TestAuditHistory)
TestDataManagementPostgres = _pg(TestDataManagement)
TestNotificationsPostgres = _pg(TestNotifications)


if __name__ == "__main__":
    unittest.main()
