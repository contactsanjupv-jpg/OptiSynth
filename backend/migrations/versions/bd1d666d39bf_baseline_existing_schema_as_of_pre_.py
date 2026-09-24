"""baseline: existing schema as of pre-Phase-1

This migration mirrors backend/app/models/schema.sql EXACTLY as it existed
before Phase 1 -- it is a snapshot, not a change. It exists so Alembic has
a revision 0 to build on top of.

IMPORTANT -- how this is meant to be used:
  - Against a FRESH database (new dev environment, CI, a new customer
    deployment): `alembic upgrade head` runs this for real and creates
    every table from scratch, equivalent to what init_db() used to do.
  - Against an EXISTING database that already has this schema (e.g. your
    real local data/rdopt.db with real project/team data in it): run
    `alembic stamp bd1d666d39bf` instead of `upgrade` -- this tells
    Alembic "this schema already exists, start tracking from here"
    WITHOUT re-running any SQL or touching existing rows. Never run
    `upgrade` against a database that already has these tables; the
    CREATE TABLE statements will fail because the tables already exist,
    which is a safe failure (nothing is silently overwritten), but stamp
    is the correct, intended path.

Revision ID: bd1d666d39bf
Revises:
Create Date: 2026-09-18 23:46:00.577868

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bd1d666d39bf'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
            created_at TEXT NOT NULL,
            UNIQUE (user_id, organization_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_memberships_org ON memberships(organization_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
            token TEXT NOT NULL UNIQUE,
            invited_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_invitations_org ON invitations(organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            objective TEXT,
            target_metric TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('maximize', 'minimize')),
            feature_columns_json TEXT NOT NULL DEFAULT '[]',
            constraints_json TEXT NOT NULL DEFAULT '[]',
            target_value REAL,
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'running', 'completed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_projects_org ON projects(organization_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_datasets_project ON datasets(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_datasets_org ON datasets(organization_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            dataset_id INTEGER REFERENCES datasets(id) ON DELETE SET NULL,
            features_json TEXT NOT NULL,
            target_value REAL NOT NULL,
            constraint_values_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL CHECK (source IN ('historical', 'recommended')),
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_experiments_project ON experiments(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_experiments_org ON experiments(organization_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            rank INTEGER NOT NULL,
            features_json TEXT NOT NULL,
            predicted_value REAL NOT NULL,
            uncertainty_std REAL NOT NULL,
            feasibility_probability REAL NOT NULL,
            acquisition_score REAL NOT NULL,
            generated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_recommendations_project ON recommendations(project_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS model_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            run_type TEXT NOT NULL CHECK (run_type IN ('backtest', 'model_metrics')),
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_model_runs_project ON model_runs(project_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            generated_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            kind TEXT NOT NULL DEFAULT 'optimization_summary',
            file_path TEXT NOT NULL,
            n_historical_rows INTEGER,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_reports_project ON reports(project_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER REFERENCES organizations(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_logs(organization_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL UNIQUE REFERENCES organizations(id) ON DELETE CASCADE,
            plan TEXT NOT NULL DEFAULT 'trial' CHECK (plan IN ('trial', 'pilot', 'enterprise')),
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'past_due', 'canceled')),
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            current_period_end TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)


def downgrade() -> None:
    # Intentionally not implemented: downgrading past the baseline means
    # dropping every table in the application, which is never something
    # that should happen by accident. If you genuinely need to tear down
    # a database entirely, do it explicitly outside of Alembic.
    raise NotImplementedError(
        "Refusing to downgrade past the baseline -- this would drop every "
        "table in the application. If this is truly intended, do it "
        "explicitly (DROP TABLE statements you write and review by hand), "
        "not via `alembic downgrade`."
    )
