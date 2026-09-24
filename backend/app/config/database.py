"""
Database connection layer using SQLAlchemy Core (not the full ORM -- see
README "Why SQLAlchemy Core, not the ORM" for the reasoning). This is the
ONLY module that creates a SQLAlchemy Engine. Every repository imports
`engine` and `db_transaction`/`db_connection` from here rather than
creating its own connection, so there is exactly one place that knows
whether we're talking to SQLite or PostgreSQL.

Portability: repositories write SQL with named bind parameters
(`:param_name`) via `sqlalchemy.text()`, which SQLAlchemy translates
correctly for both the SQLite and PostgreSQL DBAPI drivers -- unlike raw
DBAPI code (which would need `?` for SQLite and `%s` for psycopg2), this
layer of repository code does not change when DATABASE_URL switches from
`sqlite:///...` (local dev) to `postgresql+psycopg2://...` (production).

Schema versioning: as of Phase 1, schema changes are made via versioned
Alembic migrations under backend/migrations/versions/, not by hand-editing
models/schema.sql. `init_db()` below still executes schema.sql directly
and is kept ONLY because backend/tests/test_api.py's setUpClass depends on
it for fast, from-scratch test-database creation -- it is NOT how the
running application initializes its schema anymore (see
verify_migrations_at_head(), which backend/app/main.py's lifespan calls
instead). Do not add new tables to schema.sql going forward; add a new
Alembic migration and, if you want schema.sql to stay a readable snapshot
of the full schema, regenerate it separately -- the two are allowed to
drift for now since schema.sql is documentation, not the source of truth.
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from backend.app.config.settings import settings


def _make_engine() -> Engine:
    connect_args = {}
    if settings.DATABASE_URL.startswith("sqlite"):
        # Needed because FastAPI may serve a request on a different thread
        # than the one that opened the connection; PostgreSQL doesn't need
        # this flag at all.
        connect_args = {"check_same_thread": False}
    return create_engine(settings.DATABASE_URL, connect_args=connect_args, future=True)


engine: Engine = _make_engine()


@contextmanager
def db_transaction():
    """Use as `with db_transaction() as conn:` for INSERT/UPDATE/DELETE --
    commits on success, rolls back and re-raises on any exception."""
    with engine.begin() as conn:
        yield conn


@contextmanager
def db_connection():
    """Use as `with db_connection() as conn:` for read-only SELECTs."""
    with engine.connect() as conn:
        yield conn


def init_db() -> None:
    """Creates all tables/indexes from models/schema.sql if they don't
    already exist. Safe to call on every app startup.

    NOTE: schema.sql is written in portable ANSI SQL plus SQLite's
    AUTOINCREMENT keyword. For a real PostgreSQL deployment, use the
    Alembic migration path described in the README instead of this
    function -- `init_db()` is a local-dev convenience, not a migration
    tool (no versioning, no rollback)."""
    import os
    schema_path = os.path.join(os.path.dirname(__file__), "..", "models", "schema.sql")
    with open(schema_path, "r") as f:
        schema_sql = f.read()

    statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


class MigrationsNotAtHeadError(Exception):
    """Raised on app startup if the database's current Alembic revision
    doesn't match the latest migration defined in code. Fails loudly and
    immediately rather than letting the app start against a schema the
    code doesn't actually match -- the same fail-closed philosophy as
    settings.py refusing to start with a missing secret."""


def verify_migrations_at_head() -> None:
    """Checks the database's current Alembic revision against the latest
    migration script on disk. Called from main.py's lifespan on every
    startup, REPLACING the old init_db()-on-every-startup pattern for the
    real running app. Does not run any migration itself -- an operator
    must run `alembic upgrade head` explicitly (or `alembic stamp <rev>`
    for a database that already has the schema from before Alembic was
    introduced) before starting the app against a database that isn't
    current."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext

    backend_dir = os.path.join(os.path.dirname(__file__), "..", "..")
    alembic_cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    script = ScriptDirectory.from_config(alembic_cfg)
    head_revision = script.get_current_head()

    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        current_revision = context.get_current_revision()

    if current_revision != head_revision:
        raise MigrationsNotAtHeadError(
            f"Database is at migration revision {current_revision!r}, but "
            f"the code's latest migration is {head_revision!r}. Run "
            "`alembic upgrade head` (from backend/) before starting the "
            "app, or `alembic stamp head` if this database already has "
            "the current schema from before Alembic was introduced."
        )
