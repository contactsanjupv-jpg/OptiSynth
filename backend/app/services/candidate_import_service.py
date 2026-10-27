"""
Candidate CSV import: preview and import.

  preview_candidate_import  -- reads the file, shows the mapping / units / row problems; writes nothing.
  import_candidates         -- the same analysis, then stores EVERY candidate in one transaction, only when
                               the user has explicitly confirmed and not a single problem was found.

Both run exactly the same analysis, and every row goes through
`change_case_service.validate_candidate_inputs` -- the function manual "Add candidate" uses -- so there is
no second, weaker validation path. Importing never ranks anything and never generates a report: the
rankings that existed before the import are left as history and are reported as out of date by the
existing ranking-currency logic (a candidate added after a ranking), until the user runs ranking again.
"""
import hashlib
import json

from backend.app.repositories import candidate_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import (
    candidate_import_rules as rules, change_case_lifecycle_rules, change_case_rules, change_case_service,
)

RANKING_REMINDER = (
    "These candidates are imported but not ranked yet. Run ranking again before using any ranking or generating "
    "a report: an earlier ranking does not include them."
)


def _blocked_reason(case: dict):
    """Why nothing can be imported into this diagnostic right now, or None."""
    try:
        change_case_lifecycle_rules.check_not_archived(case)
        change_case_rules.check_can_add_candidate(case["status"])
    except ValidationError as exc:
        return exc.message
    return None


def _analyse(organization_id: int, case: dict, file_bytes: bytes, options: dict) -> dict:
    spec = json.loads(case["qualification_spec_json"])
    feature_columns = list(spec.get("feature_columns") or [])
    parsed = rules.parse_csv(file_bytes)
    headers = parsed["headers"]

    errors = list(parsed["problems"])  # row-numbered structure problems; they do not stop the other rows being checked
    name_column, mapping, mapping_problems = rules.resolve_mapping(headers, feature_columns, options)
    units, unit_problems = rules.resolve_units(spec, options)
    header_duplicates = [e for e in parsed["problems"] if e.get("column") in parsed["duplicate_headers"] and e["row"]]
    blocking = mapping_problems + unit_problems + header_duplicates
    errors += mapping_problems + unit_problems

    entries, preview_rows, bad_rows = [], [], set()
    if not blocking:
        existing = [c["candidate_name"] for c in candidate_repo.list_candidates_for_change_case(
            organization_id, case["id"], include_archived=True)]
        first_row_of = {}
        for r in parsed["rows"]:
            row, cells = r["row"], r["cells"]
            before = len(errors)
            name = cells[name_column].strip()
            features = {}
            if not name:
                errors.append(rules.error("The candidate name is missing.", row, name_column))
            elif len(name) > rules.MAX_NAME_LENGTH:
                errors.append(rules.error(f"The candidate name is longer than {rules.MAX_NAME_LENGTH} characters.", row, name_column))
            else:
                key = change_case_rules.normalize_candidate_name(name)
                if key in first_row_of:
                    errors.append(rules.error(
                        f"Duplicate name {name!r}: the same name (ignoring case and spaces) is already on row "
                        f"{first_row_of[key]} of this file.", row, name_column))
                else:
                    first_row_of[key] = row
            for f, col in mapping.items():
                value, message = rules.parse_number(cells[col], f)
                if message:
                    errors.append(rules.error(message, row, col))
                else:
                    features[f] = value
            if len(errors) == before:  # the row is well-formed: now the same candidate rules a typed candidate meets
                try:
                    converted, input_json = change_case_service.validate_candidate_inputs(
                        spec, name, features, units, existing)
                except ValidationError as exc:
                    errors.append(rules.error(exc.message, row, None))
                else:
                    entries.append((name, converted, input_json))
            if len(errors) > before:
                bad_rows.add(row)
            if len(preview_rows) < rules.PREVIEW_ROWS:
                preview_rows.append({"row": row, "name": name, "ok": row not in bad_rows,
                                     "values": {f: cells[col] for f, col in mapping.items()}})

    mapped_columns = {c for c in mapping.values() if c} | ({name_column} if name_column else set())
    return {
        "spec": spec, "headers": headers, "name_column": name_column, "mapping": mapping, "units": units,
        "suggested_mapping": rules.suggest_mapping(headers, feature_columns),
        "ignored_columns": [h for h in headers if h and h not in mapped_columns],
        "expected_units": {f: (spec.get("units") or {}).get(f) for f in feature_columns},
        "errors": errors, "entries": entries, "preview_rows": preview_rows,
        "row_count": len(parsed["rows"]), "blank_rows_skipped": parsed["blank_rows_skipped"],
    }


def preview_candidate_import(organization_id: int, change_case_id: int, filename: str, file_bytes: bytes,
                              raw_options) -> dict:
    case = change_case_service.get_change_case_or_404(organization_id, change_case_id)
    options = rules.parse_options(raw_options)
    a = _analyse(organization_id, case, file_bytes, options)
    blocked = _blocked_reason(case)
    ok = not a["errors"] and blocked is None and bool(a["entries"])
    return {
        "filename": filename, "sha256": hashlib.sha256(file_bytes).hexdigest(),
        "ok": ok, "blocked_reason": blocked,
        "headers": a["headers"], "row_count": a["row_count"], "blank_rows_skipped": a["blank_rows_skipped"],
        "name_column": a["name_column"], "mapping": a["mapping"], "suggested_mapping": a["suggested_mapping"],
        "ignored_columns": a["ignored_columns"],
        "expected_units": a["expected_units"], "units": a["units"],
        "preview_rows": a["preview_rows"],
        "would_import": len(a["entries"]) if not a["errors"] else 0,
        "error_count": len(a["errors"]), "errors": a["errors"][:rules.MAX_REPORTED_ERRORS],
    }


def import_candidates(organization_id: int, change_case_id: int, filename: str, file_bytes: bytes, raw_options) -> dict:
    case = change_case_service.get_change_case_or_404(organization_id, change_case_id)
    change_case_lifecycle_rules.check_not_archived(case)
    change_case_rules.check_can_add_candidate(case["status"])
    options = rules.parse_options(raw_options)
    if options.get("confirm") is not True:
        raise ValidationError("Nothing was imported: the import was not confirmed. Review the preview and confirm it.",
                              field="confirm")
    a = _analyse(organization_id, case, file_bytes, options)
    if a["errors"]:
        raise ValidationError(
            f"Nothing was imported: {len(a['errors'])} problem(s) found. "
            + rules.summarize_errors(a["errors"]) + " Fix the CSV and try again.", field="file")
    entries = a["entries"]

    def guard(case_row: dict, existing_names: list) -> None:
        # Re-checked INSIDE the write transaction against the diagnostic as it is at this instant.
        change_case_lifecycle_rules.check_not_archived(case_row)
        change_case_rules.check_can_add_candidate(case_row["status"])
        taken = list(existing_names)
        for name, _properties, _input_json in entries:
            change_case_rules.check_candidate_name_unique(name, taken)
            taken.append(name)

    ids = candidate_repo.create_candidates_batch(organization_id, change_case_id, entries, guard)
    return {
        "imported_count": len(ids),
        "candidates": [candidate_repo.get_candidate(organization_id, i) for i in ids],
        "sha256": hashlib.sha256(file_bytes).hexdigest(),
        "ranking_notice": RANKING_REMINDER,
    }
