"""add archived_at to change_cases

change_cases.archived_at: when set, the diagnostic is ARCHIVED -- hidden from the default list and
read-only, but fully retained (evidence, rankings, outcomes, reports all stay). It is deliberately
a separate marker from `status`: archiving says "stop showing / editing this", not "this diagnostic
reached a lifecycle stage", so a draft, active or completed diagnostic can each be archived and
restored without losing or inventing a status.

Purely additive and NULLABLE: every existing row stays un-archived.

Revision ID: d4a7f1c20e95
Revises: c9e61a4d2b88
Create Date: 2026-10-15 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4a7f1c20e95'
down_revision: Union[str, None] = 'c9e61a4d2b88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE change_cases ADD COLUMN archived_at TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE change_cases DROP COLUMN archived_at")