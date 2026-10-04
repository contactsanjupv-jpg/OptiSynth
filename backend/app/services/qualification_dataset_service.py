"""
Qualification-dataset ingestion. Deliberately a close structural mirror of
services/dataset_service.py rather than a shared/refactored module -- per
PROJECT_ARCHITECTURE.md's explicit rule, the two product domains are kept
fully separable. Same security discipline as the original:

- Never trusts the uploaded filename for storage.
- Stored under UPLOAD_ROOT, org-scoped path from a server-derived
  organization_id only.
- CSV via the stdlib csv module; XLSX / DOCX / PDF only through
  evidence_extraction (read-only parsing of values, no macros, no network).
- Row-count and byte-size caps enforced before real work.
- Every value coerced with float() inside try/except -- a malformed
  cell is a per-row error, never a crash or a silent zero.
"""

import json
import os
import secrets

from backend.app.config.settings import settings
from backend.app.repositories import qualification_dataset_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import change_case_rules, evidence_extraction, evidence_rules

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


def _check_spec_and_file(qualification_spec: dict, filename: str, file_bytes: bytes) -> str:
    """The size / shape checks that run before any real work. Returns the
    file kind (csv / xlsx / docx / pdf)."""
    if not qualification_spec.get("feature_columns") or not qualification_spec.get("target_metric"):
        raise ValidationError(
            "This change case's qualification_spec must define 'feature_columns' "
            "and 'target_metric' before a dataset can be uploaded.",
            field="qualification_spec",
        )
    kind = evidence_extraction.file_kind(filename)
    if len(file_bytes) == 0:
        raise ValidationError("The uploaded file is empty.")
    if len(file_bytes) > settings.MAX_UPLOAD_BYTES:
        raise ValidationError(
            f"File is too large (max {settings.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
        )
    return kind


def _parse_options(options):
    """options may arrive as a JSON string (multipart form) or a dict."""
    if options is None or options == "":
        return None
    if isinstance(options, str):
        try:
            return json.loads(options)
        except ValueError:
            raise ValidationError("Dataset review options are not valid JSON.", field="options")
    return options


def _analyze_upload(qualification_spec: dict, filename: str, file_bytes: bytes, options_raw):
    """The single intake path for every file type: extract -> (reviewer-chosen
    table) -> the B2/B3 evidence gate. Returns (review, rows_out, errors, kind).
    Nothing here stores anything."""
    kind = _check_spec_and_file(qualification_spec, filename, file_bytes)
    options = _parse_options(options_raw)
    selection = evidence_extraction.load_selection(
        filename, file_bytes, options if isinstance(options, dict) else {},
    )
    if selection["headers"] is None:
        # Could not resolve which table to use without guessing: a review of
        # findings only, no rows.
        return evidence_rules.blocked_review(selection["flags"]), [], [], kind
    review, rows_out, errors = evidence_rules.analyze_rows(
        qualification_spec, selection["headers"], selection["data_rows"], options,
        row_refs=selection["row_refs"],
    )
    review["flags"] = selection["flags"] + review["flags"]
    review["source"] = selection["source"]
    return review, rows_out, errors, kind


def extract_evidence_tables(qualification_spec: dict, filename: str, file_bytes: bytes) -> dict:
    """Step 1 of the review screen: what tables does this file contain, where
    are they, and how well does each one's first row resemble the columns this
    case needs? Display-only -- nothing is selected, mapped or stored."""
    _check_spec_and_file(qualification_spec, filename, file_bytes)
    listing = evidence_extraction.list_tables(filename, file_bytes)
    spec_units = qualification_spec.get("units") or {}
    required = list(qualification_spec["feature_columns"]) + [qualification_spec["target_metric"]]
    return {
        "file_name": filename,
        "file_kind": listing["kind"],
        "file_sha256": evidence_rules.file_sha256(file_bytes),
        "warnings": listing["warnings"],
        "required_columns": [
            {"name": c, "role": "target" if c == qualification_spec["target_metric"] else "feature",
             "unit": spec_units.get(c)}
            for c in required
        ],
        "tables": [evidence_extraction.table_summary(t, required) for t in listing["tables"]],
    }


def preview_qualification_csv(
    qualification_spec: dict,
    filename: str,
    file_bytes: bytes,
    options=None,
) -> dict:
    """Runs the full evidence-intake review (table selection, column mapping,
    units, test conditions, conflicts, data quality) WITHOUT storing
    anything, and returns the review: proposed mappings, every flag, and
    whether the file would be accepted as-is. Works for every supported file
    type despite the historical function name."""
    review, rows_out, errors, _ = _analyze_upload(qualification_spec, filename, file_bytes, options)
    blocking = evidence_rules.blocking_flags(review["flags"])
    result = {
        "would_be_accepted": not blocking and bool(rows_out),
        "file_sha256": evidence_rules.file_sha256(file_bytes),
        "headers": [m["source_header"] for m in review.get("column_mapping", {}).values()],
        "review": review,
        "errors": errors[:20],
    }
    if rows_out:
        duplicate_count, constant_columns = change_case_rules.compute_data_quality_metrics(
            rows_out, qualification_spec["feature_columns"],
        )
        result["data_quality_status"] = change_case_rules.classify_data_quality(
            historical_row_count=len(rows_out),
            duplicate_count=duplicate_count,
            constant_columns=constant_columns,
        )
    return result


def ingest_qualification_csv(
    organization_id: int,
    change_case_id: int,
    uploaded_by_user_id: int,
    qualification_spec: dict,
    filename: str,
    file_bytes: bytes,
    options=None,
) -> dict:
    """qualification_spec must contain 'feature_columns' (list[str]) and
    'target_metric' (str). Despite the historical name this stores evidence
    from any supported file (.csv / .xlsx / .docx / .pdf).

    The file first passes the evidence gate. Any blocking flag (no / ambiguous
    table, unconfirmed or ambiguous column mapping, missing / mixed /
    incompatible units, differing test conditions, conflicting observations,
    ambiguous numbers) REFUSES the upload: nothing is stored and nothing
    reaches the model. What is stored is only the canonical, validated rows
    (each with its source reference), the review record that explains every
    decision, and the original file under a server-generated name."""
    feature_columns = qualification_spec.get("feature_columns")
    review, rows_out, errors, kind = _analyze_upload(qualification_spec, filename, file_bytes, options)
    if evidence_rules.blocking_flags(review["flags"]):
        raise ValidationError(evidence_rules.summarize_blocking(review["flags"]), field="dataset")
    # Priority 4: data-quality calculations (shared with the ranking/report
    # gates in change_case_service via change_case_rules).
    duplicate_count, constant_columns = change_case_rules.compute_data_quality_metrics(
        rows_out, feature_columns,
    )
    if not rows_out:
        raise ValidationError(
            "No valid numeric rows found in the uploaded file."
        )
    review["file_sha256"] = evidence_rules.file_sha256(file_bytes)
    stored_filename = f"{secrets.token_hex(16)}.{kind}"
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
        review_json=json.dumps(review),
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
        "review": review,
        "warnings": [f for f in review["flags"] if f["severity"] == evidence_rules.WARNING],
    }


def list_dataset_summaries(organization_id: int, change_case_id: int) -> list:
    """The evidence inventory: every upload for a change case with its source
    file, hash, tables used and review counts. The LATEST one is what ranking
    uses."""
    out = []
    for i, d in enumerate(qualification_dataset_repo.list_datasets_for_change_case(organization_id, change_case_id)):
        review = json.loads(d["review_json"]) if d.get("review_json") else None
        out.append({
            "id": d["id"],
            "original_filename": d["original_filename"],
            "uploaded_at": d["created_at"],
            "row_count": d["row_count"],
            "is_current": i == 0,  # list is newest-first
            "has_review_record": review is not None,
            "file_sha256": (review or {}).get("file_sha256"),
            "source": (review or {}).get("source"),
            "counts": (review or {}).get("counts"),
            "warning_count": len([f for f in (review or {}).get("flags", []) if f.get("severity") == "warning"]),
        })
    return out
