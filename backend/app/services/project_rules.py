"""
Pure business rules with NO fastapi/sqlalchemy/pydantic import -- only
schemas.errors, which is itself pure stdlib. This module exists
specifically so its logic can be unit-tested in environments where the
web framework and ORM aren't installed (see backend/tests/test_project_rules.py,
which DOES run in this project's sandbox, unlike anything touching FastAPI).
"""
from backend.app.schemas.errors import ValidationError

STRUCTURAL_PROJECT_FIELDS = {"feature_columns", "constraints"}


def check_structural_edit_allowed(fields: dict, experiment_count: int) -> None:
    """Raises ValidationError if the caller is trying to change
    feature_columns or constraints on a project that already has
    historical experiments -- doing so would silently desync the stored
    experiment rows (each keyed to the OLD column names) from the
    project's new declared shape, corrupting every downstream calculation
    (recommendations, backtest, model metrics) without any visible error.
    If you need to change the variables on a project with data, the safe
    path is a new project."""
    attempted = STRUCTURAL_PROJECT_FIELDS & fields.keys()
    if attempted and experiment_count > 0:
        raise ValidationError(
            "Can't change feature columns or constraints on a project that already has "
            "experiment data -- this would desync existing rows from the new column names. "
            "Create a new project instead, or remove the uploaded data first.",
            field=next(iter(attempted)),
        )