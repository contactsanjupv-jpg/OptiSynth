"""add per-row provenance and candidate input record

qualification_experiments.provenance_json: where each historical row came from
(file, sheet/table/page, source row) -- written at intake by the evidence
review. candidate_substitutes.input_json: what was ENTERED for a candidate
(value + unit) and what it became after exact conversion to the case's
canonical units.

Purely additive and nullable: existing rows simply have no provenance / input
record and are reported as such.

Revision ID: b8d52f1a3c77
Revises: a7c41e9b2d10
Create Date: 2026-10-01 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b8d52f1a3c77'
down_revision: Union[str, None] = 'a7c41e9b2d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE qualification_experiments ADD COLUMN provenance_json TEXT")
    op.execute("ALTER TABLE candidate_substitutes ADD COLUMN input_json TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE candidate_substitutes DROP COLUMN input_json")
    op.execute("ALTER TABLE qualification_experiments DROP COLUMN provenance_json")
