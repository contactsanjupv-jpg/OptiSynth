"""candidate inputs_version, candidate archived_at, prediction inputs_version

Level 1D: candidates can be edited and removed from an analysis without destroying history.

* candidate_substitutes.inputs_version -- bumped every time a candidate's feature values (or the
  units they were entered in) change. Starts at 0 for every existing candidate.
* predictions.inputs_version -- the candidate's inputs_version at the moment the ranking ran. A
  prediction is "for the current inputs" only while the two numbers are equal; after an edit it is
  kept as history but never shown as if it described the new values. Existing predictions get 0,
  which equals every existing candidate's 0, so nothing already stored is suddenly flagged.
* candidate_substitutes.archived_at -- a candidate "removed from the analysis": hidden from the
  default list, excluded from ranking, but retained with its predictions, recommended experiments
  and recorded outcomes.
* candidate_substitutes.restored_at -- when such a candidate was last put back. Together with
  archived_at it lets the product notice that the SET of candidates changed after a ranking ran.

Purely additive; nothing is dropped or rewritten.

Revision ID: e5b8c3a91f47
Revises: d4a7f1c20e95
Create Date: 2026-10-18 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5b8c3a91f47'
down_revision: Union[str, None] = 'd4a7f1c20e95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE candidate_substitutes ADD COLUMN inputs_version INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE candidate_substitutes ADD COLUMN archived_at TEXT")
    op.execute("ALTER TABLE candidate_substitutes ADD COLUMN restored_at TEXT")
    op.execute("ALTER TABLE predictions ADD COLUMN inputs_version INTEGER NOT NULL DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE predictions DROP COLUMN inputs_version")
    op.execute("ALTER TABLE candidate_substitutes DROP COLUMN restored_at")
    op.execute("ALTER TABLE candidate_substitutes DROP COLUMN archived_at")
    op.execute("ALTER TABLE candidate_substitutes DROP COLUMN inputs_version")