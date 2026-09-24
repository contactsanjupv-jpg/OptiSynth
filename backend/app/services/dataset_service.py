"""
Dataset ingestion. Security-sensitive by design (customer-uploaded files):

  - Never trusts the uploaded filename for storage -- generates a random
    internal filename (secrets.token_hex) and stores only that. The
    original filename is kept purely for display.
  - Files are stored under UPLOAD_ROOT (outside any source/executable
    directory) -- see config/settings.py.
  - Uses Python's stdlib `csv` module for parsing -- never `eval`/`exec`,
    never a formula-evaluating spreadsheet engine.
  - Enforces a row-count cap and a byte-size cap before doing any real
    work. Note the size check happens after FastAPI/Starlette has already
    buffered the upload into memory (there is no FastAPI equivalent to
    Flask's MAX_CONTENT_LENGTH that rejects an oversized body before it's
    read) -- see README "Known limitations" for the hardening needed
    before accepting uploads from untrusted networks at scale (e.g. a
    reverse-proxy-level body-size limit in front of the app).
  - Every value is coerced with float() inside try/except -- a single
    malformed cell is reported as a per-row error, not a crash, and never
    silently coerced to zero.
"""
import csv
import io
import os
import secrets

from backend.app.config.settings import settings
from backend.app.repositories import datasets_repo, experiments_repo
from backend.app.schemas.errors import ValidationError

UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "uploads")


def _upload_dir_for_org(organization_id: int) -> str:
    # organization_id is always a server-derived int (from the session, never
    # from the request body), so this path segment can never contain ".."
    # or other path-traversal characters.
    path = os.path.join(UPLOAD_ROOT, str(organization_id))
    os.makedirs(path, exist_ok=True)
    return path


def ingest_csv(organization_id: int, project_id: int, uploaded_by_user_id: int,
                project: dict, filename: str, file_bytes: bytes) -> dict:
    if not filename.lower().endswith(".csv"):
        raise ValidationError("Only .csv files are accepted.")
    if len(file_bytes) == 0:
        raise ValidationError("The uploaded file is empty.")
    if len(file_bytes) > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File is too large (max {settings.MAX_UPLOAD_BYTES // (1024*1024)} MB)."
        )

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationError("Could not read file as UTF-8 text. Please export as a standard CSV.")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValidationError("Could not find a header row in the CSV.")

    feature_columns = project["feature_columns"]
    target_metric = project["target_metric"]
    constraint_columns = [c["column"] for c in project["constraints"]]
    required_columns = set(feature_columns + [target_metric] + constraint_columns)
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
            raise ValidationError(f"Too many rows (max {settings.MAX_UPLOAD_ROWS}).")
        try:
            features = {col: float(raw_row[col]) for col in feature_columns}
            target_value = float(raw_row[target_metric])
            constraint_values = {col: float(raw_row[col]) for col in constraint_columns}
            rows_out.append({
                "features": features,
                "target_value": target_value,
                "constraint_values": constraint_values,
            })
        except (TypeError, ValueError):
            errors.append({"row": i, "error": "Non-numeric value in a required column."})

    if not rows_out:
        raise ValidationError("No valid numeric rows found in the uploaded file.")

    stored_filename = f"{secrets.token_hex(16)}.csv"
    dest_path = os.path.join(_upload_dir_for_org(organization_id), stored_filename)
    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    dataset_id = datasets_repo.create_dataset(
        organization_id, project_id, uploaded_by_user_id, filename, stored_filename, len(rows_out)
    )
    experiments_repo.bulk_create_experiments(organization_id, project_id, dataset_id, rows_out)

    return {
        "dataset_id": dataset_id,
        "rows_ingested": len(rows_out),
        "rows_skipped": len(errors),
        "errors": errors[:20],  # cap what's returned -- never dump the whole file back
    }
