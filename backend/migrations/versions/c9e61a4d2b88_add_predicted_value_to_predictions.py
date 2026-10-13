"""add predicted_value to predictions

predictions.predicted_value: the engine's predicted target-property value for a
candidate, in the case's canonical target unit, stored beside the probability
and uncertainty from the same ranking run (same candidate, dataset version and
model version).

Purely additive and NULLABLE. Predictions stored before this revision have no
recorded value: they stay NULL and are shown as "not recorded" -- the value is
deliberately NOT back-filled, because it cannot be reconstructed without
re-running the model on possibly different data.

Revision ID: c9e61a4d2b88
Revises: b8d52f1a3c77
Create Date: 2026-10-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9e61a4d2b88'
down_revision: Union[str, None] = 'b8d52f1a3c77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE predictions ADD COLUMN predicted_value REAL")


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN predicted_value")
