"""add ingestion review record to qualification_datasets

Stores, per uploaded dataset, the record of the evidence-intake review: the
confirmed column mapping, canonical units and any explicit conversions,
declared test conditions, reviewer exclusions, replicate statistics, every
warning raised, and the SHA-256 of the stored file. It is what makes a
dataset's provenance auditable and what the report reads from.

Purely additive and nullable: existing rows (datasets uploaded before this
migration) simply have no review record and are reported as such.

Revision ID: a7c41e9b2d10
Revises: de175c637f37
Create Date: 2026-09-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7c41e9b2d10'
down_revision: Union[str, None] = 'de175c637f37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE qualification_datasets ADD COLUMN review_json TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE qualification_datasets DROP COLUMN review_json")