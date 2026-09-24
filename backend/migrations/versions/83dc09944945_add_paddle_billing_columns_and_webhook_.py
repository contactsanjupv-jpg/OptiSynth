"""add paddle billing columns and webhook event log

Adds paddle_customer_id / paddle_subscription_id to `subscriptions`,
ADDITIVELY, alongside the existing stripe_customer_id /
stripe_subscription_id columns -- per the approved plan, the old Stripe
columns are NOT dropped in this migration (Stripe billing was never
actually implemented -- billing_service.py's Stripe functions are, and
remain, NotImplementedError stubs -- so those columns are unused, but we
leave them in place rather than making a destructive change; a future,
separate migration can drop them once confirmed unused in every
environment).

Also adds paddle_webhook_events, the idempotency/audit log for incoming
Paddle webhooks: every event's paddle_event_id is UNIQUE, so processing
the same webhook twice (a normal, expected occurrence with any webhook
provider) can be detected and safely no-op'd rather than double-applying
a state change. Per PROJECT_ARCHITECTURE.md's billing rule, this table is
the ONLY thing services/billing/paddle_client.py writes to on webhook
receipt, before any internal subscription state is touched.

Revision ID: 83dc09944945
Revises: 2a47323d3b0c
Create Date: 2026-09-19 08:50:28.006382

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '83dc09944945'
down_revision: Union[str, None] = '2a47323d3b0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_exists(table: str, column: str) -> bool:
    """
    SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so a bare
    ADD COLUMN is not idempotent -- it fails if the column is already
    there (e.g. this migration's own downgrade() intentionally leaves
    these columns in place, or a partially-applied migration is retried).
    Checked explicitly instead of relying on the statement to be safe to
    re-run.
    """
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def upgrade() -> None:
    if not _column_exists("subscriptions", "paddle_customer_id"):
        op.execute("ALTER TABLE subscriptions ADD COLUMN paddle_customer_id TEXT")
    if not _column_exists("subscriptions", "paddle_subscription_id"):
        op.execute("ALTER TABLE subscriptions ADD COLUMN paddle_subscription_id TEXT")

    op.execute("""
        CREATE TABLE IF NOT EXISTS paddle_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paddle_event_id TEXT NOT NULL UNIQUE,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            processing_status TEXT NOT NULL DEFAULT 'received' CHECK (processing_status IN ('received', 'processed', 'failed')),
            received_at TEXT NOT NULL,
            processed_at TEXT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_paddle_events_status ON paddle_webhook_events(processing_status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paddle_webhook_events")
    # SQLite historically couldn't DROP COLUMN directly; modern SQLite
    # (3.35+, which this project already relies on for RETURNING clauses
    # per schema.sql's own header) does support it, but we deliberately
    # don't reverse the ADD COLUMN here -- an unused nullable column is
    # harmless, whereas getting a DROP COLUMN wrong on a live database is
    # not a risk worth taking in a downgrade path that will rarely, if
    # ever, actually be exercised.
    pass
