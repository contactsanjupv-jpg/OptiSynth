"""add change_case qualification domain

Adds the new regulatory-driven substitute-qualification domain
(change_cases -> candidate_substitutes -> predictions ->
recommended_experiments -> qualification_outcomes) alongside the
existing projects/optimization domain. Nothing existing is touched,
renamed, or dropped -- this is purely additive, per
PROJECT_ARCHITECTURE.md's non-destructive-migration rule.

qualification_outcomes is designed to be append-only at the application
layer (see change_case_rules.py) -- it is the core proprietary dataset
the company's moat depends on, so its rows are never UPDATEd, only
inserted.

Revision ID: 2a47323d3b0c
Revises: bd1d666d39bf
Create Date: 2026-09-19 08:49:34.752975

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '2a47323d3b0c'
down_revision: Union[str, None] = 'bd1d666d39bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE change_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            trigger_type TEXT NOT NULL CHECK (trigger_type IN ('regulatory_restriction', 'supplier_loss', 'component_obsolescence', 'other')),
            restricted_substance TEXT,
            qualification_spec_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'completed')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_change_cases_org ON change_cases(organization_id)")

    # Reuses the exact same shape/discipline as the existing `datasets`
    # table (random server-generated stored_filename, never the client's
    # original filename) -- kept as a distinct table rather than reusing
    # `datasets` directly so the two product domains stay fully separable.
    op.execute("""
        CREATE TABLE qualification_datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            change_case_id INTEGER NOT NULL REFERENCES change_cases(id) ON DELETE CASCADE,
            uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_qual_datasets_change_case ON qualification_datasets(change_case_id)")
    op.execute("CREATE INDEX idx_qual_datasets_org ON qualification_datasets(organization_id)")

    op.execute("""
        CREATE TABLE candidate_substitutes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            change_case_id INTEGER NOT NULL REFERENCES change_cases(id) ON DELETE CASCADE,
            candidate_name TEXT NOT NULL,
            properties_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_candidates_change_case ON candidate_substitutes(change_case_id)")
    op.execute("CREATE INDEX idx_candidates_org ON candidate_substitutes(organization_id)")

    # dataset_version_id + model_version give the reproducibility chain
    # PROJECT_ARCHITECTURE.md requires: we must always be able to answer
    # "exactly which data and model produced this recommendation."
    op.execute("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            candidate_id INTEGER NOT NULL REFERENCES candidate_substitutes(id) ON DELETE CASCADE,
            dataset_version_id INTEGER NOT NULL REFERENCES qualification_datasets(id) ON DELETE RESTRICT,
            model_version TEXT NOT NULL,
            predicted_probability REAL NOT NULL,
            uncertainty_std REAL NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_predictions_candidate ON predictions(candidate_id)")
    op.execute("CREATE INDEX idx_predictions_org ON predictions(organization_id)")

    op.execute("""
        CREATE TABLE recommended_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            candidate_id INTEGER NOT NULL REFERENCES candidate_substitutes(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_rec_experiments_candidate ON recommended_experiments(candidate_id)")

    # Append-only at the application layer: change_case_rules.py must
    # never expose an update path for this table, only insert. See the
    # module docstring there for the enforced rule.
    op.execute("""
        CREATE TABLE qualification_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            candidate_id INTEGER NOT NULL REFERENCES candidate_substitutes(id) ON DELETE CASCADE,
            recommended_experiment_id INTEGER REFERENCES recommended_experiments(id) ON DELETE SET NULL,
            actual_result TEXT,
            passed_spec INTEGER NOT NULL CHECK (passed_spec IN (0, 1)),
            recorded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_qual_outcomes_candidate ON qualification_outcomes(candidate_id)")
    op.execute("CREATE INDEX idx_qual_outcomes_org ON qualification_outcomes(organization_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS qualification_outcomes")
    op.execute("DROP TABLE IF EXISTS recommended_experiments")
    op.execute("DROP TABLE IF EXISTS predictions")
    op.execute("DROP TABLE IF EXISTS candidate_substitutes")
    op.execute("DROP TABLE IF EXISTS qualification_datasets")
    op.execute("DROP TABLE IF EXISTS change_cases")
