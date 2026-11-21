"""Level 5: server-side session revocation. Logout must end the session on the server, so a copy of the signed
cookie stops working; other sessions are untouched; the state is in the database, so it survives a restart.
Run:  python3 -m unittest backend.tests.test_session_revocation -v
(PostgreSQL scenarios: set OPTISYNTH_TEST_POSTGRES_URL as in test_migration_portability.)"""
import os
import subprocess
import sys
import unittest
import uuid
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from alembic import command

from backend.app.config.settings import settings
from backend.app.security import login_throttle
from backend.app.security.sessions import create_session_token, read_session_token
from backend.tests._api_base import IsolatedApiTestCase
from backend.tests.test_migration_portability import POSTGRES_URL, _config, _UseDatabase

PASSWORD = "correct-horse-battery-staple"
BEFORE_SESSIONS = "e5b8c3a91f47"
SESSIONS_REVISION = "f6c9d4e2a1b3"  # the revision that added user_sessions; later revisions sit on top of it


class TestSessionRevocation(IsolatedApiTestCase):
    dir_name = "_test_env_session_revocation"

    def setUp(self):
        login_throttle.throttle.reset()

    # ------------------------------------------------------------ helpers
    def _signup(self, label):
        org_id, client = self.org_and_client(label)
        return client, f"{self.dir_name}{self._n}@example.com", org_id

    def _login(self, email):
        client = self.client_factory()
        r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        return client

    @staticmethod
    def _token(client):
        return client.cookies.get(settings.SESSION_COOKIE_NAME)

    def _replay(self, token):
        """A copy of the cookie sent by a browser that never logged in (an empty cookie jar)."""
        return self.client_factory().get("/api/auth/me", headers={"Cookie": f"{settings.SESSION_COOKIE_NAME}={token}"})

    def _rows(self, where="1=1", **params):
        from backend.app.config.database import db_connection
        with db_connection() as conn:
            return [dict(r) for r in conn.execute(text(f"SELECT * FROM user_sessions WHERE {where} ORDER BY id"), params).mappings()]

    # ------------------------------------------------------------ issuing
    def test_signup_and_every_login_store_a_separate_session(self):
        client, email, _ = self._signup("Issue")
        first = read_session_token(self._token(client))["sid"]
        second_client, third_client = self._login(email), self._login(email)
        sids = {first, read_session_token(self._token(second_client))["sid"], read_session_token(self._token(third_client))["sid"]}
        self.assertEqual(len(sids), 3)
        stored = {r["session_id"] for r in self._rows()}
        self.assertTrue(sids <= stored)
        row = self._rows("session_id = :s", s=first)[0]
        self.assertIsNone(row["revoked_at"])
        lifetime = datetime.fromisoformat(row["expires_at"]) - datetime.fromisoformat(row["created_at"])
        self.assertAlmostEqual(lifetime.total_seconds(), settings.SESSION_LIFETIME_HOURS * 3600, delta=5)

    def test_the_session_id_is_random_and_long(self):
        client, _, _ = self._signup("Random")
        sid = read_session_token(self._token(client))["sid"]
        self.assertGreaterEqual(len(sid), 32)

    # ------------------------------------------------------------ logout and replay
    def test_logout_then_replaying_the_copied_cookie_is_401(self):
        client, _, _ = self._signup("Replay")
        token = self._token(client)
        self.assertEqual(self._replay(token).status_code, 200)             # the copy works while the session is live
        self.assertEqual(client.post("/api/auth/logout").status_code, 200)
        r = self._replay(token)                                             # the attacker's copy, signature still valid
        self.assertEqual(r.status_code, 401, r.text)
        self.assertEqual(r.json()["detail"], "Session expired or invalid.")
        self.assertIsNotNone(read_session_token(token), "premise: the cookie is still correctly signed and unexpired")
        self.assertEqual(self.client_factory().get("/api/change-cases", headers={"Cookie": f"{settings.SESSION_COOKIE_NAME}={token}"}).status_code, 401)

    def test_the_session_row_is_marked_revoked_not_deleted(self):
        client, _, _ = self._signup("Row")
        sid = read_session_token(self._token(client))["sid"]
        client.post("/api/auth/logout")
        row = self._rows("session_id = :s", s=sid)[0]
        self.assertIsNotNone(row["revoked_at"])

    def test_logging_out_twice_or_without_a_cookie_is_401_and_changes_nothing(self):
        client, email, _ = self._signup("Twice")
        token = self._token(client)
        other = self._login(email)
        client.post("/api/auth/logout")
        again = self.client_factory().post("/api/auth/logout", headers={"Cookie": f"{settings.SESSION_COOKIE_NAME}={token}"})
        self.assertEqual(again.status_code, 401)
        self.assertEqual(self.client_factory().post("/api/auth/logout").status_code, 401)
        self.assertEqual(other.get("/api/auth/me").status_code, 200)

    def test_logging_in_again_after_logout_gives_a_working_new_session(self):
        client, email, _ = self._signup("Relogin")
        old = self._token(client)
        client.post("/api/auth/logout")
        fresh = self._login(email)
        self.assertEqual(fresh.get("/api/auth/me").status_code, 200)
        self.assertEqual(self._replay(old).status_code, 401)
        self.assertNotEqual(old, self._token(fresh))

    # ------------------------------------------------------------ other sessions are preserved
    def test_one_users_other_session_survives_a_logout(self):
        client_a, email, _ = self._signup("Concurrent")
        client_b, client_c = self._login(email), self._login(email)
        token_a, token_b = self._token(client_a), self._token(client_b)
        client_a.post("/api/auth/logout")
        self.assertEqual(self._replay(token_a).status_code, 401)
        self.assertEqual(self._replay(token_b).status_code, 200)
        self.assertEqual(client_b.get("/api/auth/me").status_code, 200)
        self.assertEqual(client_c.get("/api/auth/me").status_code, 200)
        client_b.post("/api/auth/logout")
        self.assertEqual(client_c.get("/api/auth/me").status_code, 200)   # still alive after a second, independent logout

    def test_another_users_session_survives_a_logout(self):
        client_a, _, _ = self._signup("UserA")
        client_b, _, _ = self._signup("UserB")
        token_b = self._token(client_b)
        client_a.post("/api/auth/logout")
        self.assertEqual(self._replay(token_b).status_code, 200)
        self.assertEqual(client_b.get("/api/auth/me").status_code, 200)

    # ------------------------------------------------------------ the stored session is what counts
    def test_a_correctly_signed_token_without_a_session_id_is_refused(self):
        client, _, org_id = self._signup("Legacy")
        user_id = client.get("/api/auth/me").json()["user"]["id"]
        legacy = create_session_token(user_id, org_id)   # exactly what the cookie looked like before this change
        self.assertEqual(self._replay(legacy).status_code, 401)

    def test_unknown_session_id_or_a_session_id_of_another_user_is_refused(self):
        client_a, _, org_a = self._signup("OwnerA")
        client_b, _, org_b = self._signup("OwnerB")
        user_a = client_a.get("/api/auth/me").json()["user"]["id"]
        sid_b = read_session_token(self._token(client_b))["sid"]
        self.assertEqual(self._replay(create_session_token(user_a, org_a, "no-such-session")).status_code, 401)
        self.assertEqual(self._replay(create_session_token(user_a, org_a, sid_b)).status_code, 401)   # B's id, A's identity
        self.assertEqual(self._replay(create_session_token(user_a, org_b, read_session_token(self._token(client_a))["sid"])).status_code, 401)

    def test_a_tampered_or_garbage_cookie_is_still_401(self):
        client, _, _ = self._signup("Tamper")
        token = self._token(client)
        for bad in (token[:-2] + ("AA" if not token.endswith("AA") else "BB"), "garbage", ""):
            self.assertEqual(self._replay(bad).status_code, 401, bad)

    # ------------------------------------------------------------ expiration
    def test_an_expired_stored_session_is_refused_even_though_the_signature_is_fine(self):
        from backend.app.config.database import db_transaction
        client, _, _ = self._signup("Expire")
        token = self._token(client)
        sid = read_session_token(token)["sid"]
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with db_transaction() as conn:
            conn.execute(text("UPDATE user_sessions SET expires_at = :p WHERE session_id = :s"), {"p": past, "s": sid})
        self.assertEqual(self._replay(token).status_code, 401)
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_a_session_just_inside_its_expiry_is_still_accepted(self):
        from backend.app.config.database import db_transaction
        client, _, _ = self._signup("Boundary")
        sid = read_session_token(self._token(client))["sid"]
        soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with db_transaction() as conn:
            conn.execute(text("UPDATE user_sessions SET expires_at = :p WHERE session_id = :s"), {"p": soon, "s": sid})
        self.assertEqual(client.get("/api/auth/me").status_code, 200)

    def test_an_unreadable_expiry_fails_closed(self):
        from backend.app.config.database import db_transaction
        client, _, _ = self._signup("BadExpiry")
        sid = read_session_token(self._token(client))["sid"]
        with db_transaction() as conn:
            conn.execute(text("UPDATE user_sessions SET expires_at = 'not-a-date' WHERE session_id = :s"), {"s": sid})
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_the_signature_lifetime_still_applies(self):
        client, _, _ = self._signup("Signature")
        token = self._token(client)
        from unittest import mock
        from itsdangerous import TimestampSigner
        later = datetime.now(timezone.utc).timestamp() + settings.SESSION_LIFETIME_HOURS * 3600 + 60
        with mock.patch.object(TimestampSigner, "get_timestamp", return_value=int(later)):
            self.assertEqual(self._replay(token).status_code, 401)
        self.assertEqual(self._replay(token).status_code, 200)

    def test_expired_rows_are_cleared_when_the_next_session_starts(self):
        from backend.app.config.database import db_transaction
        client, email, _ = self._signup("Purge")
        sid = read_session_token(self._token(client))["sid"]
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with db_transaction() as conn:
            conn.execute(text("UPDATE user_sessions SET expires_at = :p WHERE session_id = :s"), {"p": past, "s": sid})
        live = self._login(email)
        self.assertEqual(self._rows("session_id = :s", s=sid), [])
        self.assertEqual(live.get("/api/auth/me").status_code, 200)

    def test_a_revoked_session_stays_revoked_until_it_expires(self):
        client, email, _ = self._signup("Stay")
        sid = read_session_token(self._token(client))["sid"]
        client.post("/api/auth/logout")
        self._login(email)  # starting other sessions does not clear a still-unexpired revoked row
        self.assertEqual(len(self._rows("session_id = :s AND revoked_at IS NOT NULL", s=sid)), 1)

    # ------------------------------------------------------------ existing protections are unchanged
    def test_a_deactivated_user_still_loses_access_immediately(self):
        from backend.app.config.database import db_transaction
        client, email, _ = self._signup("Deactivate")
        with db_transaction() as conn:
            conn.execute(text("UPDATE users SET is_active = 0 WHERE email = :e"), {"e": email})
        self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_deleting_the_user_removes_their_sessions(self):
        from backend.app.config.database import db_transaction
        client, email, _ = self._signup("Cascade")
        user_id = client.get("/api/auth/me").json()["user"]["id"]
        self.assertTrue(self._rows("user_id = :u", u=user_id))
        with db_transaction() as conn:
            conn.execute(text("DELETE FROM memberships WHERE user_id = :u"), {"u": user_id})
            conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user_id})
        self.assertEqual(self._rows("user_id = :u", u=user_id), [])

    # ------------------------------------------------------------ restart
    def test_revocation_survives_an_engine_restart_in_this_process(self):
        from backend.app.config import database
        client_a, email, _ = self._signup("Restart")
        client_b = self._login(email)
        token_a, token_b = self._token(client_a), self._token(client_b)
        client_a.post("/api/auth/logout")
        database.engine.dispose()
        database.engine = database._make_engine()
        self.assertEqual(self._replay(token_a).status_code, 401)
        self.assertEqual(self._replay(token_b).status_code, 200)

    def test_revocation_survives_a_real_new_process(self):
        """A second Python process (a fresh start of the application) sees the same revoked and live sessions."""
        client_a, email, _ = self._signup("Process")
        client_b = self._login(email)
        token_a, token_b = self._token(client_a), self._token(client_b)
        client_a.post("/api/auth/logout")
        code = (
            "import sys\n"
            "from fastapi.testclient import TestClient\n"
            "from backend.app.main import app\n"
            "c = TestClient(app)\n"
            "for token in sys.argv[1:]:\n"
            f"    r = c.get('/api/auth/me', headers={{'Cookie': '{settings.SESSION_COOKIE_NAME}=' + token}}); print(r.status_code, r.text[:80].replace(' ', '_'))\n")
        # the values THIS process is actually using (settings was read when the module was first imported)
        env = {**os.environ, "SECRET_KEY": settings.SECRET_KEY, "PASSWORD_PEPPER": settings.PASSWORD_PEPPER,
               "DATABASE_URL": settings.DATABASE_URL}
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        done = subprocess.run([sys.executable, "-c", code, token_a, token_b], capture_output=True, text=True, cwd=root,
                              env={**env, "PYTHONPATH": root}, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr[-500:])
        self.assertEqual([line.split()[0] for line in done.stdout.splitlines()], ["401", "200"], done.stdout + done.stderr[-300:])


@unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")
class TestSessionRevocationPostgres(TestSessionRevocation):
    """Every scenario above, with the application running against a real PostgreSQL schema built by the migrations."""
    dir_name = "_test_env_session_revocation_pg"

    @classmethod
    def setUpClass(cls):
        from backend.app.config import database
        from backend.tests._api_base import unit_aware_client
        cls.schema = f"sessapi_{uuid.uuid4().hex[:10]}"
        cls.admin = create_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA "{cls.schema}"'))
        url = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={cls.schema}"}).render_as_string(hide_password=False)
        cls._saved = (settings.DATABASE_URL, database.engine, os.environ.get("DATABASE_URL"))
        os.environ["DATABASE_URL"] = url
        settings.DATABASE_URL = url
        database.engine = database._make_engine()
        command.upgrade(_config(), "head")
        from backend.app.main import app
        cls.client_factory = staticmethod(lambda: unit_aware_client(app))
        cls._n = 0

    @classmethod
    def tearDownClass(cls):
        from backend.app.config import database
        database.engine.dispose()
        settings.DATABASE_URL, database.engine, env_url = cls._saved
        if env_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = env_url
        with cls.admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{cls.schema}" CASCADE'))
        cls.admin.dispose()


# ---------------------------------------------------------------- migration behaviour (SQLite and PostgreSQL)
class _MigrationScenarios:
    url = None

    def new_engine(self):
        raise NotImplementedError

    def _seed_user(self, engine):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO organizations (name, created_at) VALUES ('Mig Co', 't')"))
            org = conn.execute(text("SELECT id FROM organizations")).scalar()
            conn.execute(text("INSERT INTO users (email, password_hash, display_name, is_active, created_at) "
                              "VALUES ('m@example.com', 'hash', 'M', 1, 't')"))
            user = conn.execute(text("SELECT id FROM users")).scalar()
        return org, user

    def test_upgrade_adds_the_table_keeps_data_and_downgrade_removes_it(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), BEFORE_SESSIONS)
                self.assertNotIn("user_sessions", inspect(engine).get_table_names())
                org, user = self._seed_user(engine)

                command.upgrade(_config(), SESSIONS_REVISION)
                insp = inspect(engine)
                self.assertIn("user_sessions", insp.get_table_names())
                self.assertEqual({c["name"] for c in insp.get_columns("user_sessions")},
                                 {"id", "session_id", "user_id", "organization_id", "created_at", "expires_at", "revoked_at"})
                self.assertTrue({"idx_user_sessions_user", "idx_user_sessions_expires"} <=
                                {i["name"] for i in insp.get_indexes("user_sessions")})
                with engine.connect() as conn:  # existing data untouched; nobody is signed in until they log in again
                    self.assertEqual(conn.execute(text("SELECT email FROM users")).scalar(), "m@example.com")
                    self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM user_sessions")).scalar(), 0)
                    row = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                self.assertEqual(row, SESSIONS_REVISION)

                command.downgrade(_config(), BEFORE_SESSIONS)
                self.assertNotIn("user_sessions", inspect(engine).get_table_names())
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM users")).scalar(), 1)

                command.upgrade(_config(), "head")   # re-upgrade after a downgrade
                self.assertIn("user_sessions", inspect(engine).get_table_names())
        finally:
            engine.dispose()

    def test_constraints_are_enforced(self):
        engine = self.new_engine()
        try:
            with _UseDatabase(self.url):
                command.upgrade(_config(), "head")
                org, user = self._seed_user(engine)
                insert = ("INSERT INTO user_sessions (session_id, user_id, organization_id, created_at, expires_at) "
                          "VALUES (:s, :u, :o, 't', 't2')")
                with engine.begin() as conn:
                    conn.execute(text(insert), {"s": "abc", "u": user, "o": org})
                with self.assertRaises(IntegrityError):          # session ids are unique
                    with engine.begin() as conn:
                        conn.execute(text(insert), {"s": "abc", "u": user, "o": org})
                with self.assertRaises(IntegrityError):          # must belong to a real user
                    with engine.begin() as conn:
                        conn.execute(text(insert), {"s": "xyz", "u": user + 999, "o": org})
                with engine.begin() as conn:                     # deleting the user removes their sessions
                    conn.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT COUNT(*) FROM user_sessions")).scalar(), 0)
        finally:
            engine.dispose()

    def test_a_server_stamped_at_this_revision_reports_it(self):
        with _UseDatabase(self.url):
            command.upgrade(_config(), SESSIONS_REVISION)
            engine = self.new_engine()
            try:
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT version_num FROM alembic_version")).scalar(), SESSIONS_REVISION)
            finally:
                engine.dispose()


class TestSqliteSessionMigration(_MigrationScenarios, unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(os.path.dirname(__file__), f"_test_env_sess_mig_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.dir)
        self.url = f"sqlite:///{self.dir}/m.db"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def new_engine(self):
        engine = create_engine(self.url)

        @event.listens_for(engine, "connect")
        def _fk(dbapi_connection, record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return engine


@unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")
class TestPostgresSessionMigration(_MigrationScenarios, unittest.TestCase):
    def setUp(self):
        self.schema = f"sessmig_{uuid.uuid4().hex[:10]}"
        self.admin = create_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        url = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={self.schema}"})
        self.url = url.render_as_string(hide_password=False)

    def tearDown(self):
        with self.admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        self.admin.dispose()

    def new_engine(self):
        return create_engine(self.url)


if __name__ == "__main__":
    unittest.main()
