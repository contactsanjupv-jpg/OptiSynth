"""
Migration portability: the Alembic chain must build and upgrade the schema on BOTH supported databases
-- SQLite (local development, the test suite) and PostgreSQL (production: README, .env.example,
requirements.txt).

Root cause this guards against: the early migrations were written in SQLite dialect
(`INTEGER PRIMARY KEY AUTOINCREMENT`, `PRAGMA table_info`, 4-byte-on-PostgreSQL `REAL`), so
`alembic upgrade head` failed on the very first statement against PostgreSQL.

What runs where
  * Always (no database server needed): chain integrity, offline SQL generation for both dialects, and
    a static guard that no migration sends SQLite-only SQL straight to the database.
  * Always: the real-SQLite scenarios (fresh database, every revision one at a time, an existing
    database with data upgraded to head, downgrade + re-upgrade, float and id behaviour).
  * Only when OPTISYNTH_TEST_POSTGRES_URL is set: the SAME scenarios against a real PostgreSQL server,
    plus a PostgreSQL-vs-SQLite schema comparison. Without it they are reported as SKIPPED (never as
    passed).

Running the PostgreSQL scenarios
    OPTISYNTH_TEST_POSTGRES_URL=postgresql+psycopg2://user:password@localhost:5432/somedb \\
        python3 -m unittest backend.tests.test_migration_portability -v
The role needs CREATE SCHEMA on that database. Every test creates its own throwaway schema
(migtest_<random>), points the connection's search_path at it, and drops it afterwards -- nothing in
`public` or any other schema is read or changed.
"""
import ast
import io
import os
import re
import shutil
import unittest
import uuid

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from backend.app.config.settings import settings

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")
VERSIONS_DIR = os.path.join(BACKEND_DIR, "migrations", "versions")
POSTGRES_URL = os.environ.get("OPTISYNTH_TEST_POSTGRES_URL")
BASELINE = "bd1d666d39bf"
PRE_ARCHIVE = "83dc09944945"  # a mid-history revision used as "the schema an existing deployment is on"


def _config() -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "migrations"))
    return cfg


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(_config())


def _revisions_oldest_first() -> list:
    return [r.revision for r in reversed(list(_script().walk_revisions()))]


class _UseDatabase:
    """env.py reads settings.DATABASE_URL every time Alembic runs; point it at `url` for the block."""

    def __init__(self, url: str):
        self.url = url

    def __enter__(self):
        self.saved = settings.DATABASE_URL
        settings.DATABASE_URL = self.url

    def __exit__(self, *exc):
        settings.DATABASE_URL = self.saved


def _offline_sql(url: str) -> str:
    """The SQL `alembic upgrade head --sql` would emit for that database, with no connection made."""
    cfg = _config()
    cfg.output_buffer = io.StringIO()
    with _UseDatabase(url):
        command.upgrade(cfg, "head", sql=True)
    return cfg.output_buffer.getvalue()


# --------------------------------------------------------------------------------------------------
# No database needed
# --------------------------------------------------------------------------------------------------
class TestMigrationChain(unittest.TestCase):
    def test_one_linear_chain_with_a_single_head(self):
        script = _script()
        heads = script.get_heads()
        self.assertEqual(len(heads), 1, f"more than one head -> the history has branched: {heads}")
        files = [f for f in os.listdir(VERSIONS_DIR) if f.endswith(".py") and not f.startswith("_")]
        walked = list(script.walk_revisions())
        self.assertEqual(len(walked), len(files), "a migration file is missing from the chain (or the chain has extras)")
        children = {}
        for rev in walked:
            for parent in ([rev.down_revision] if isinstance(rev.down_revision, str) else (rev.down_revision or [])):
                children.setdefault(parent, []).append(rev.revision)
        self.assertTrue(all(len(c) == 1 for c in children.values()), f"a revision has two children: {children}")
        self.assertEqual(walked[-1].down_revision, None, "the oldest revision must be the baseline")
        self.assertEqual(walked[-1].revision, BASELINE)
        self.assertEqual(script.get_current_head(), walked[0].revision)

    def test_every_down_revision_exists(self):
        known = {r.revision for r in _script().walk_revisions()}
        for rev in _script().walk_revisions():
            if rev.down_revision:
                self.assertIn(rev.down_revision, known)


class TestPortableSql(unittest.TestCase):
    def test_postgresql_offline_sql_has_no_sqlite_dialect(self):
        sql = _offline_sql("postgresql+psycopg2://u:p@localhost/db")
        self.assertNotIn("AUTOINCREMENT", sql)
        self.assertNotIn("PRAGMA", sql.upper())
        self.assertIsNone(re.search(r"\bREAL\b", sql), "a 4-byte REAL column would round stored values")
        self.assertEqual(sql.count("GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"), 20)
        self.assertEqual(sql.count("DOUBLE PRECISION"), 10)
        self.assertIn("ALTER TABLE subscriptions ADD COLUMN paddle_customer_id TEXT", sql)  # offline: assumed absent

    def test_sqlite_offline_sql_is_unchanged(self):
        sql = _offline_sql("sqlite:///unused.db")
        self.assertEqual(sql.count("INTEGER PRIMARY KEY AUTOINCREMENT"), 20)
        self.assertEqual(len(re.findall(r"\bREAL\b", sql)), 10)
        self.assertNotIn("IDENTITY", sql)
        self.assertNotIn("DOUBLE PRECISION", sql)

    def test_a_percent_encoded_database_url_does_not_break_alembic(self):
        """Passwords with special characters are percent-encoded in DATABASE_URL (p@ss/w%rd -> p%40ss%2Fw%25rd).
        Alembic's config parser treats "%" as interpolation, so env.py must escape it; unescaped, every
        migration aborted with "invalid interpolation syntax"."""
        url = "postgresql+psycopg2://user:p%40ss%2Fw%25rd@localhost/db"
        sql = _offline_sql(url)  # runs env.py with that URL
        self.assertIn("CREATE TABLE IF NOT EXISTS organizations", sql)

    def test_no_migration_sends_sqlite_only_sql_straight_to_the_database(self):
        """Future migrations: a literal `op.execute("... AUTOINCREMENT / REAL ...")` or a PRAGMA is exactly
        what broke PostgreSQL. Write dialect-neutral SQL, or send it through the migration's `_execute()`
        (see the helper in the baseline migration) / SQLAlchemy constructs."""
        bad_words = (re.compile(r"AUTOINCREMENT", re.I), re.compile(r"\bPRAGMA\b", re.I), re.compile(r"\bREAL\b"))
        offenders = []
        for name in sorted(os.listdir(VERSIONS_DIR)):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(open(os.path.join(VERSIONS_DIR, name)).read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = ast.unparse(node.func)
                if callee not in ("op.execute", "exec_driver_sql", "bind.exec_driver_sql", "conn.exec_driver_sql"):
                    continue
                for arg in node.args:
                    literal = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
                    if literal and any(w.search(literal) for w in bad_words):
                        offenders.append(f"{name}:{node.lineno} {callee}(...)")
        self.assertEqual(offenders, [], "SQLite-only SQL reaches the database unconverted: " + "; ".join(offenders))


# --------------------------------------------------------------------------------------------------
# Real databases: the same scenarios on SQLite (always) and PostgreSQL (when a server is provided)
# --------------------------------------------------------------------------------------------------
SEED_ORG = "INSERT INTO organizations (name, created_at) VALUES ('Existing Co', '2026-01-01T00:00:00+00:00')"


class _Scenarios:
    """Mixed into one TestCase per backend. Subclasses provide `url` (the database under test) and
    `new_engine()`; each test gets a database/schema of its own."""

    url: str

    def new_engine(self):
        return create_engine(self.url)

    def _upgrade(self, revision="head"):
        with _UseDatabase(self.url):
            command.upgrade(_config(), revision)

    def _downgrade(self, revision):
        with _UseDatabase(self.url):
            command.downgrade(_config(), revision)

    def _current(self):
        engine = self.new_engine()
        try:
            with engine.connect() as conn:
                return MigrationContext.configure(conn).get_current_revision()
        finally:
            engine.dispose()

    def _query(self, sql, **params):
        engine = self.new_engine()
        try:
            with engine.begin() as conn:
                result = conn.execute(text(sql), params)
                return result.all() if result.returns_rows else None
        finally:
            engine.dispose()

    def _seed_existing_deployment(self):
        """Rows an existing deployment would hold at the PRE_ARCHIVE schema (org, user-less case, evidence
        upload, candidate, prediction, subscription)."""
        engine = self.new_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text(SEED_ORG))
                org = conn.execute(text("SELECT id FROM organizations")).scalar()
                conn.execute(text(
                    "INSERT INTO subscriptions (organization_id, created_at, updated_at) "
                    "VALUES (:o, 't', 't')"), {"o": org})
                conn.execute(text(
                    "INSERT INTO change_cases (organization_id, name, trigger_type, qualification_spec_json, "
                    "status, created_at, updated_at) VALUES (:o, 'Old case', 'other', '{}', 'draft', 't', 't')"),
                    {"o": org})
                case = conn.execute(text("SELECT id FROM change_cases")).scalar()
                conn.execute(text(
                    "INSERT INTO qualification_datasets (organization_id, change_case_id, original_filename, "
                    "stored_filename, row_count, created_at) VALUES (:o, :c, 'f.csv', 's.csv', 3, 't')"),
                    {"o": org, "c": case})
                dataset = conn.execute(text("SELECT id FROM qualification_datasets")).scalar()
                conn.execute(text(
                    "INSERT INTO candidate_substitutes (organization_id, change_case_id, candidate_name, "
                    "properties_json, created_at) VALUES (:o, :c, 'Old candidate', '{\"a\": 1.5}', 't')"),
                    {"o": org, "c": case})
                cand = conn.execute(text("SELECT id FROM candidate_substitutes")).scalar()
                conn.execute(text(
                    "INSERT INTO predictions (organization_id, candidate_id, dataset_version_id, model_version, "
                    "predicted_probability, uncertainty_std, created_at) "
                    "VALUES (:o, :k, :d, 'v1', 0.75, 2.5, 't')"), {"o": org, "k": cand, "d": dataset})
        finally:
            engine.dispose()

    # ---- scenarios -------------------------------------------------------------------------------
    def test_fresh_database_upgrades_to_head(self):
        self._upgrade()
        self.assertEqual(self._current(), _script().get_current_head())
        engine = self.new_engine()
        try:
            tables = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        for expected in ("organizations", "users", "memberships", "change_cases", "qualification_datasets",
                         "qualification_experiments", "candidate_substitutes", "predictions",
                         "recommended_experiments", "qualification_outcomes", "subscriptions",
                         "paddle_webhook_events", "audit_logs", "alembic_version"):
            self.assertIn(expected, tables)

    def test_every_revision_applies_one_at_a_time(self):
        for revision in _revisions_oldest_first():
            self._upgrade(revision)
            self.assertEqual(self._current(), revision)

    def test_existing_database_with_data_upgrades_and_keeps_every_row(self):
        self._upgrade(PRE_ARCHIVE)
        self._seed_existing_deployment()
        self._upgrade()
        self.assertEqual(self._current(), _script().get_current_head())
        self.assertEqual(self._query("SELECT name FROM organizations"), [("Existing Co",)])
        self.assertEqual(self._query("SELECT candidate_name, properties_json FROM candidate_substitutes"),
                         [("Old candidate", '{"a": 1.5}')])
        self.assertEqual(self._query("SELECT predicted_probability, uncertainty_std FROM predictions"), [(0.75, 2.5)])
        # columns added after PRE_ARCHIVE exist and describe old rows honestly
        self.assertEqual(self._query("SELECT inputs_version, archived_at, restored_at FROM candidate_substitutes"),
                         [(0, None, None)])
        self.assertEqual(self._query("SELECT inputs_version, predicted_value FROM predictions"), [(0, None)])
        self.assertEqual(self._query("SELECT archived_at FROM change_cases"), [(None,)])
        self.assertEqual(self._query("SELECT review_json FROM qualification_datasets"), [(None,)])
        self.assertEqual(self._query("SELECT paddle_customer_id FROM subscriptions"), [(None,)])
        # and the database keeps handing out ids after the upgrade
        self._query(SEED_ORG.replace("Existing Co", "New Co"))
        ids = [r[0] for r in self._query("SELECT id FROM organizations ORDER BY id")]
        self.assertEqual(len(ids), 2)
        self.assertLess(ids[0], ids[1])

    def test_downgrade_to_baseline_then_upgrade_again(self):
        """83dc's downgrade leaves its columns behind on purpose; re-upgrading must therefore skip an
        ADD COLUMN whose column already exists -- the inspector-based check that replaced PRAGMA."""
        self._upgrade()
        self._downgrade(BASELINE)
        self.assertEqual(self._current(), BASELINE)
        self._upgrade()
        self.assertEqual(self._current(), _script().get_current_head())

    def test_ids_are_assigned_by_the_database(self):
        self._upgrade()
        self._query(SEED_ORG)
        self._query(SEED_ORG.replace("Existing Co", "Second Co"))
        ids = [r[0] for r in self._query("SELECT id FROM organizations ORDER BY id")]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(isinstance(i, int) for i in ids))
        self.assertLess(ids[0], ids[1])

    def test_floating_point_values_round_trip_exactly(self):
        """A 4-byte float would turn 0.1234567890123 into 0.12345679 -- silently changing stored predictions
        and targets. All numeric columns must keep full double precision."""
        self._upgrade()
        precise = 0.1234567890123
        big = 1234567.891234567
        engine = self.new_engine()
        try:
            with engine.begin() as conn:
                conn.execute(text(SEED_ORG))
                org = conn.execute(text("SELECT id FROM organizations")).scalar()
                conn.execute(text(
                    "INSERT INTO change_cases (organization_id, name, trigger_type, qualification_spec_json, status, "
                    "created_at, updated_at) VALUES (:o, 'c', 'other', '{}', 'draft', 't', 't')"), {"o": org})
                case = conn.execute(text("SELECT id FROM change_cases")).scalar()
                conn.execute(text(
                    "INSERT INTO qualification_datasets (organization_id, change_case_id, original_filename, "
                    "stored_filename, row_count, created_at) VALUES (:o, :c, 'f', 's', 1, 't')"), {"o": org, "c": case})
                dataset = conn.execute(text("SELECT id FROM qualification_datasets")).scalar()
                conn.execute(text(
                    "INSERT INTO candidate_substitutes (organization_id, change_case_id, candidate_name, "
                    "properties_json, created_at) VALUES (:o, :c, 'k', '{}', 't')"), {"o": org, "c": case})
                cand = conn.execute(text("SELECT id FROM candidate_substitutes")).scalar()
                conn.execute(text(
                    "INSERT INTO predictions (organization_id, candidate_id, dataset_version_id, model_version, "
                    "predicted_probability, uncertainty_std, predicted_value, created_at) "
                    "VALUES (:o, :k, :d, 'v', :p, :u, :v, 't')"),
                    {"o": org, "k": cand, "d": dataset, "p": precise, "u": 1e-9, "v": big})
                conn.execute(text(
                    "INSERT INTO qualification_experiments (organization_id, change_case_id, qualification_dataset_id, "
                    "features_json, target_value, created_at) VALUES (:o, :c, :d, '{}', :t, 't')"),
                    {"o": org, "c": case, "d": dataset, "t": big})
                row = conn.execute(text(
                    "SELECT predicted_probability, uncertainty_std, predicted_value FROM predictions")).one()
                target = conn.execute(text("SELECT target_value FROM qualification_experiments")).scalar()
        finally:
            engine.dispose()
        self.assertEqual(tuple(row), (precise, 1e-9, big))
        self.assertEqual(target, big)

    def test_foreign_keys_and_checks_are_enforced_by_the_schema(self):
        self._upgrade()
        engine = self.new_engine()
        try:
            from sqlalchemy.exc import IntegrityError
            with self.assertRaises(IntegrityError):  # CHECK (plan IN (...)) came through the dialect rewrite
                with engine.begin() as conn:
                    conn.execute(text(SEED_ORG))
                    org = conn.execute(text("SELECT id FROM organizations")).scalar()
                    conn.execute(text(
                        "INSERT INTO subscriptions (organization_id, plan, created_at, updated_at) "
                        "VALUES (:o, 'bogus-plan', 't', 't')"), {"o": org})
        finally:
            engine.dispose()


class TestSqliteMigrations(_Scenarios, unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(os.path.dirname(__file__), f"_test_env_mig_sqlite_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.dir)
        self.url = f"sqlite:///{self.dir}/m.db"

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def new_engine(self):
        engine = create_engine(self.url)
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _fk(dbapi_connection, record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return engine


@unittest.skipUnless(POSTGRES_URL, "set OPTISYNTH_TEST_POSTGRES_URL to run the PostgreSQL scenarios")
class TestPostgresMigrations(_Scenarios, unittest.TestCase):
    def setUp(self):
        self.schema = f"migtest_{uuid.uuid4().hex[:10]}"
        self.admin = create_engine(POSTGRES_URL, isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        url = make_url(POSTGRES_URL).update_query_dict({"options": f"-csearch_path={self.schema}"})
        self.url = url.render_as_string(hide_password=False)

    def tearDown(self):
        with self.admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE'))
        self.admin.dispose()

    def test_postgresql_and_sqlite_end_with_the_same_tables_and_columns(self):
        self._upgrade()
        sqlite_dir = os.path.join(os.path.dirname(__file__), f"_test_env_mig_cmp_{uuid.uuid4().hex[:8]}")
        os.makedirs(sqlite_dir)
        try:
            sqlite_url = f"sqlite:///{sqlite_dir}/c.db"
            with _UseDatabase(sqlite_url):
                command.upgrade(_config(), "head")
            pg, lite = create_engine(self.url), create_engine(sqlite_url)
            try:
                ip, il = inspect(pg), inspect(lite)
                pg_tables = set(ip.get_table_names())
                lite_tables = set(il.get_table_names()) - {"sqlite_sequence"}
                self.assertEqual(pg_tables, lite_tables)
                for table in sorted(pg_tables):
                    cp, cl = ip.get_columns(table), il.get_columns(table)
                    self.assertEqual([c["name"] for c in cp], [c["name"] for c in cl], table)
                    for a, b in zip(cp, cl):
                        if a["name"] == "id":  # SQLite reports INTEGER PRIMARY KEY as nullable; it cannot be NULL
                            continue
                        self.assertEqual(a["nullable"], b["nullable"], f"{table}.{a['name']}")
                    self.assertEqual(len(ip.get_foreign_keys(table)), len(il.get_foreign_keys(table)), table)
                with pg.connect() as conn:
                    self.assertEqual(conn.execute(text(
                        "SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
                        "AND data_type = 'real'")).scalar(), 0)
            finally:
                pg.dispose()
                lite.dispose()
        finally:
            shutil.rmtree(sqlite_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
