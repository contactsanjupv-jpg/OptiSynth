import json

from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_qualification_dataset(organization_id: int, change_case_id: int, uploaded_by_user_id: int,
                                  original_filename: str, stored_filename: str, row_count: int) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO qualification_datasets
                     (organization_id, change_case_id, uploaded_by_user_id, original_filename,
                      stored_filename, row_count, created_at)
                     VALUES (:org_id, :change_case_id, :uploaded_by, :original_filename,
                             :stored_filename, :row_count, :created_at) RETURNING id"""),
            {"org_id": organization_id, "change_case_id": change_case_id, "uploaded_by": uploaded_by_user_id,
             "original_filename": original_filename, "stored_filename": stored_filename,
             "row_count": row_count, "created_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_latest_dataset_for_change_case(organization_id: int, change_case_id: int):
    """The most recently uploaded dataset for a change case -- used as the
    dataset_version_id stamped onto every prediction, so a re-upload
    naturally produces predictions tied to the NEW data, never silently
    reusing a stale version."""
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT * FROM qualification_datasets
                     WHERE organization_id = :org_id AND change_case_id = :change_case_id
                     ORDER BY id DESC LIMIT 1"""),
            {"org_id": organization_id, "change_case_id": change_case_id},
        ).mappings().first()
        return dict(row) if row else None


def get_dataset_for_change_case(organization_id: int, change_case_id: int, dataset_id: int):
    """A specific dataset version, scoped to the organization AND change
    case -- used to evaluate a prediction/report against the exact dataset
    it was generated from (predictions.dataset_version_id), never against
    whichever upload happens to be newest."""
    with db_connection() as conn:
        row = conn.execute(
            text("""SELECT * FROM qualification_datasets
                     WHERE id = :dataset_id AND organization_id = :org_id
                       AND change_case_id = :change_case_id"""),
            {"dataset_id": dataset_id, "org_id": organization_id, "change_case_id": change_case_id},
        ).mappings().first()
        return dict(row) if row else None


def list_datasets_for_change_case(organization_id: int, change_case_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM qualification_datasets
                     WHERE organization_id = :org_id AND change_case_id = :change_case_id
                     ORDER BY id DESC"""),
            {"org_id": organization_id, "change_case_id": change_case_id},
        ).mappings().all()
        return [dict(r) for r in rows]


def bulk_create_qualification_experiments(organization_id: int, change_case_id: int,
                                            qualification_dataset_id: int, rows: list) -> int:
    """rows: list of {"features": {col: value}, "target_value": float} --
    same shape as experiments_repo.bulk_create_experiments in the original
    domain, for the new domain's qualification_experiments table."""
    if not rows:
        return 0
    with db_transaction() as conn:
        created_at = now_iso()
        conn.execute(
            text("""INSERT INTO qualification_experiments
                     (organization_id, change_case_id, qualification_dataset_id,
                      features_json, target_value, created_at)
                     VALUES (:org_id, :change_case_id, :dataset_id, :features_json,
                             :target_value, :created_at)"""),
            [
                {"org_id": organization_id, "change_case_id": change_case_id,
                 "dataset_id": qualification_dataset_id, "features_json": json.dumps(r["features"]),
                 "target_value": r["target_value"], "created_at": created_at}
                for r in rows
            ],
        )
        return len(rows)


def list_experiments_for_dataset(organization_id: int, qualification_dataset_id: int):
    with db_connection() as conn:
        rows = conn.execute(
            text("""SELECT * FROM qualification_experiments
                     WHERE organization_id = :org_id AND qualification_dataset_id = :dataset_id
                     ORDER BY id ASC"""),
            {"org_id": organization_id, "dataset_id": qualification_dataset_id},
        ).mappings().all()
        return [dict(r) for r in rows]
