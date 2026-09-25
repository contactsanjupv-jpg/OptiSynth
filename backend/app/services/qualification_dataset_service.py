"""
Qualification-dataset ingestion. Deliberately a close structural mirror of
services/dataset_service.py rather than a shared/refactored module -- per
PROJECT_ARCHITECTURE.md's explicit rule, the two product domains are kept
fully separable. Same security discipline as the original:

- Never trusts the uploaded filename for storage.
- Stored under UPLOAD_ROOT, org-scoped path from a server-derived
  organization_id only.
- stdlib csv module only.
- Row-count and byte-size caps enforced before real work.
- Every value coerced with float() inside try/except -- a malformed
  cell is a per-row error, never a crash or a silent zero.
"""

import csv
import io
import os
import secrets

from backend.app.config.settings import settings
from backend.app.repositories import qualification_dataset_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import change_case_rules

UPLOAD_ROOT = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "..",
    "data",
    "qualification_uploads",
)


def _upload_dir_for_org(organization_id: int) -> str:
    # organization_id is always server-derived (never from the request
    # body), so this path segment can never contain ".." or other
    # path-traversal characters -- identical guarantee to dataset_service.py.
    path = os.path.join(UPLOAD_ROOT, str(organization_id))
    os.makedirs(path, exist_ok=True)
    return path


def ingest_qualification_csv(
    organization_id: int,
    change_case_id: int,
    uploaded_by_user_id: int,
    qualification_spec: dict,
    filename: str,
    file_bytes: bytes,
) -> dict:
    """qualification_spec must contain 'feature_columns' (list[str]) and
    'target_metric' (str) -- these define which CSV columns are the
    candidate-scoring features and which is the historical qualification
    outcome value. Set when the change case is created."""

    feature_columns = qualification_spec.get("feature_columns")
    target_metric = qualification_spec.get("target_metric")

    if not feature_columns or not target_metric:
        raise ValidationError(
            "This change case's qualification_spec must define 'feature_columns' "
            "and 'target_metric' before a dataset can be uploaded.",
            field="qualification_spec",
        )

    if not filename.lower().endswith(".csv"):
        raise ValidationError("Only .csv files are accepted.")

    if len(file_bytes) == 0:
        raise ValidationError("The uploaded file is empty.")

    if len(file_bytes) > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File is too large (max {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
        )

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError(
            "Could not read file as UTF-8 text. Please export as a standard CSV."
        )

    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise ValidationError("Could not find a header row in the CSV.")

    required_columns = set(feature_columns + [target_metric])
    missing = required_columns - set(reader.fieldnames)

    if missing:
        raise ValidationError(
            "Unable to process dataset. Please check the required columns and try again. "
            f"Missing: {', '.join(sorted(missing))}"
        )

    rows_out = []
    errors = []
    row_count = 0

    for i, raw_row in enumerate(reader, start=1):
        row_count += 1

        if row_count > settings.MAX_UPLOAD_ROWS:
            raise ValidationError(
                f"Too many rows (max {settings.MAX_UPLOAD_ROWS})."
            )

        try:
            features = {
                col: float(raw_row[col])
                for col in feature_columns
            }
            target_value = float(raw_row[target_metric])

            rows_out.append(
                {
                    "features": features,
                    "target_value": target_value,
                }
            )

        except (TypeError, ValueError):
            errors.append(
                {
                    "row": i,
                    "error": "Non-numeric value in a required column.",
                }
            )

    # Priority 4: data-quality calculations (shared with the ranking/report
    # gates in change_case_service via change_case_rules).
    duplicate_count, constant_columns = change_case_rules.compute_data_quality_metrics(
        rows_out, feature_columns,
    )

    if not rows_out:
        raise ValidationError(
            "No valid numeric rows found in the uploaded file."
        )

    stored_filename = f"{secrets.token_hex(16)}.csv"

    dest_path = os.path.join(
        _upload_dir_for_org(organization_id),
        stored_filename,
    )

    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    dataset_id = qualification_dataset_repo.create_qualification_dataset(
        organization_id,
        change_case_id,
        uploaded_by_user_id,
        filename,
        stored_filename,
        len(rows_out),
    )

    qualification_dataset_repo.bulk_create_qualification_experiments(
        organization_id,
        change_case_id,
        dataset_id,
        rows_out,
    )

    data_quality_status = change_case_rules.classify_data_quality(
        historical_row_count=len(rows_out),
        duplicate_count=duplicate_count,
        constant_columns=constant_columns,
    )

    return {
        "dataset_id": dataset_id,
        "rows_ingested": len(rows_out),
        "rows_skipped": len(errors),
        "errors": errors[:20],
        "data_quality_status": data_quality_status,
        "duplicate_rows_found": duplicate_count,
        "constant_columns": constant_columns,
    }