"""add qualification_experiments table -- missed in original Phase 2 audit

CORRECTIVE MIGRATION: the original Phase 2 implementation plan audited
the schema and concluded no new migration was needed, since
qualification_datasets already existed. That was incomplete --
qualification_datasets only stores uploaded-file METADATA (mirroring the
original domain's `datasets` table), but there was nowhere to store the
actual parsed historical rows (feature values + target/spec value per
row) that engine.rank_candidates needs as its X/y input. This mirrors the
original domain's `experiments` table shape exactly, for the new domain.

Purely additive -- nothing existing is touched.

Revision ID: de175c637f37
Revises: 83dc09944945
Create Date: 2026-09-20 09:24:36.496602

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'de175c637f37'
down_revision: Union[str, None] = '83dc09944945'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE qualification_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            change_case_id INTEGER NOT NULL REFERENCES change_cases(id) ON DELETE CASCADE,
            qualification_dataset_id INTEGER NOT NULL REFERENCES qualification_datasets(id) ON DELETE CASCADE,
            features_json TEXT NOT NULL,
            target_value REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_qual_experiments_change_case ON qualification_experiments(change_case_id)")
    op.execute("CREATE INDEX idx_qual_experiments_dataset ON qualification_experiments(qualification_dataset_id)")
    op.execute("CREATE INDEX idx_qual_experiments_org ON qualification_experiments(organization_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qualification_experiments")
