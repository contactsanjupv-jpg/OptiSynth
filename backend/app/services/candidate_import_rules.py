"""
Candidate CSV import -- pure rules (no fastapi / sqlalchemy imports), in the same style as the other
`*_rules.py` modules.

What lives here: reading the CSV bytes with the stdlib `csv` module, mapping CSV columns to the
diagnostic's fields, reading numbers strictly, and resolving the units the user declared. What does NOT
live here: the checks a candidate itself must pass (complete, known fields, finite, units convertible,
unique name). Those are the existing candidate rules, applied to every imported row by
`change_case_service.validate_candidate_inputs` -- the SAME function manual "Add candidate" uses -- so an
imported candidate can never be accepted on weaker terms than a typed one.

Row numbers in every message are the CSV file's own line numbers (the header is row 1), the same way the
evidence review numbers rows.
"""
import csv
import io
import math
import re

from backend.app.schemas.errors import ValidationError

DEFAULT_NAME_COLUMN = "candidate_name"
MAX_IMPORT_ROWS = 1000          # candidates per file; bounds the work one request can ask for
MAX_REPORTED_ERRORS = 200       # the response lists at most this many; the total is always reported
PREVIEW_ROWS = 10
MAX_NAME_LENGTH = 200           # same limit as AddCandidateRequest.name

ALLOWED_OPTION_KEYS = {"name_column", "column_mapping", "units", "confirm_canonical_units", "confirm"}

# Plain decimal or scientific notation only. Deliberately narrower than Python's float(): no "nan", "inf",
# "1_000" (underscores), unicode digits, thousands separators or surrounding text.
_NUMBER = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_NON_FINITE_WORDS = {"nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}


def error(message: str, row: int = None, column: str = None) -> dict:
    return {"row": row, "column": column, "message": message}


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------
def parse_csv(file_bytes: bytes) -> dict:
    """-> {"headers": [str], "rows": [{"row": int, "cells": {header: str}}], "blank_rows_skipped": int,
           "problems": [error dicts]}.
    Raises ValidationError only when the file cannot be read as a CSV table at all. Structural problems that
    the user can fix in the source file (duplicate headers, ragged rows) are returned as `problems`."""
    if not file_bytes or not file_bytes.strip():
        raise ValidationError("The file is empty.")
    try:
        text = file_bytes.decode("utf-8-sig")  # also removes a UTF-8 byte-order mark
    except UnicodeDecodeError:
        raise ValidationError("Could not read the file as UTF-8 text. Export it as a standard CSV (UTF-8).")
    if "\x00" in text:
        raise ValidationError("The file contains binary data, so it is not a CSV text file.")

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    headers, header_line, rows, problems, blank = None, None, [], [], 0
    try:
        for raw in reader:
            if not raw:  # a truly blank line
                continue
            if headers is None:
                headers, header_line = [c.strip() for c in raw], reader.line_num
                continue
            line = reader.line_num
            if len(rows) >= MAX_IMPORT_ROWS:
                raise ValidationError(
                    f"Too many candidates in one file (max {MAX_IMPORT_ROWS}). Split the file and import it in parts.")
            if all(not c.strip() for c in raw):
                blank += 1
                continue
            if len(raw) > len(headers) and any(c.strip() for c in raw[len(headers):]):
                problems.append(error(f"This row has {len(raw)} values but the header has {len(headers)} columns.", line))
                continue
            if len(raw) < len(headers):
                problems.append(error(f"This row has {len(raw)} values but the header has {len(headers)} columns.", line))
                continue
            rows.append({"row": line, "cells": {h: raw[i] for i, h in enumerate(headers)}})
    except csv.Error as exc:
        raise ValidationError(f"The file is not a valid CSV (row {reader.line_num}): {exc}")
    if headers is None:
        raise ValidationError("Could not find a header row in the CSV.")
    if not rows and not problems:
        raise ValidationError("The file has a header row but no candidates.")

    seen, dupes = set(), []
    for h in headers:
        if h and h in seen and h not in dupes:
            dupes.append(h)
        seen.add(h)
    for h in dupes:
        problems.append(error(f"The header contains the column name {h!r} more than once, so it is unclear which "
                              "column to read. Rename or remove one of them in the CSV.", header_line, h))
    return {"headers": headers, "rows": rows, "blank_rows_skipped": blank, "problems": problems,
            "duplicate_headers": dupes}


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------
def suggest_mapping(headers: list, feature_columns: list) -> dict:
    """Exact (case-sensitive) header matches only; {feature: header or None}."""
    present = set(headers)
    return {f: (f if f in present else None) for f in feature_columns}


def resolve_mapping(headers: list, feature_columns: list, options: dict) -> tuple:
    """-> (name_column or None, {feature: header or None}, problems).
    A user-supplied mapping wins; features without one fall back to an exact header match; nothing else is
    ever inferred. Every mapped column must exist, no column may feed two fields, and the name column cannot
    also be a feature."""
    problems = []
    header_set = set(headers)
    name_column = options.get("name_column", DEFAULT_NAME_COLUMN)
    if not isinstance(name_column, str) or name_column not in header_set:
        problems.append(error(
            f"The candidate-name column {name_column!r} is not in the file (columns found: "
            f"{', '.join(repr(h) for h in headers if h) or 'none'}). Choose which column holds the candidate names.",
            None, str(name_column)))
        name_column = None

    given = options.get("column_mapping") or {}
    if not isinstance(given, dict):
        raise ValidationError("column_mapping must be an object of {field: column}.")
    for f in given:
        if f not in feature_columns:
            problems.append(error(f"column_mapping names {f!r}, which is not an input field of this diagnostic "
                                  f"(fields: {', '.join(feature_columns)}).", None, f))
    suggested = suggest_mapping(headers, feature_columns)
    mapping = {}
    for f in feature_columns:
        col = given[f] if f in given else suggested[f]
        if col in (None, ""):
            problems.append(error(f"No column in the file is mapped to the field {f!r}. Choose its column.", None, f))
            col = None
        elif not isinstance(col, str) or col not in header_set:
            problems.append(error(f"Field {f!r} is mapped to column {col!r}, which is not in the file.", None, f))
            col = None
        mapping[f] = col
    used = {}
    for f, col in mapping.items():
        if col is None:
            continue
        if col == name_column:
            problems.append(error(f"The column {col!r} is used both as the candidate name and for {f!r}.", None, col))
        if col in used:
            problems.append(error(
                f"The column {col!r} is mapped to both {used[col]!r} and {f!r}. Each field needs its own column.", None, col))
        used.setdefault(col, f)
    return name_column, mapping, problems


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
def parse_number(raw: str, field: str) -> tuple:
    """-> (float or None, message or None). Strict: the cell must be a plain finite number."""
    text = (raw or "").strip()
    if text == "":
        return None, f"{field}: the value is missing."
    if text.lower() in _NON_FINITE_WORDS:
        return None, f"{field}: {text!r} is not allowed. Values must be finite numbers (not NaN or infinity)."
    if not _NUMBER.match(text):
        return None, (f"{field}: {text!r} is not a plain number. Use digits with an optional decimal point "
                      "(no thousands separators, units or text).")
    value = float(text)
    if not math.isfinite(value):
        return None, f"{field}: {text!r} is too large to be a valid number."
    return value, None


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
def resolve_units(spec: dict, options: dict) -> tuple:
    """-> ({feature: unit}, problems). Units are never guessed. For every field whose diagnostic unit is
    declared, the user must either name the unit of the file's values explicitly (options["units"], converted
    later only by an exact factor) or confirm the whole file is already in the diagnostic's units
    (options["confirm_canonical_units"] is True)."""
    columns = list(spec.get("feature_columns") or [])
    canonical = spec.get("units") or {}
    declared = options.get("units") or {}
    if not isinstance(declared, dict):
        raise ValidationError("units must be an object of {field: unit}.")
    confirm = options.get("confirm_canonical_units", False)
    if confirm not in (True, False):
        raise ValidationError("confirm_canonical_units must be true or false.")
    units, problems = {}, []
    for f in declared:
        if f not in columns:
            problems.append(error(f"A unit was given for {f!r}, which is not an input field of this diagnostic.", None, f))
    for f in columns:
        raw = declared.get(f)
        explicit = raw.strip() if isinstance(raw, str) and raw.strip() else None
        if explicit is not None:
            units[f] = explicit
        elif canonical.get(f) is not None and confirm is True:
            units[f] = canonical[f]
        elif canonical.get(f) is not None:
            problems.append(error(
                f"The unit of {f!r} is not stated. This diagnostic uses {canonical[f]!r}: either confirm the file's "
                f"values are in {canonical[f]!r}, or enter the unit they are in.", None, f))
    return units, problems


def parse_options(raw) -> dict:
    """The optional JSON `options` form field -> dict. Unknown keys are refused, never ignored."""
    if raw in (None, ""):
        return {}
    import json
    try:
        options = json.loads(raw)
    except (TypeError, ValueError):
        raise ValidationError("The import options are not valid JSON.")
    if not isinstance(options, dict):
        raise ValidationError("The import options must be a JSON object.")
    unknown = sorted(set(options) - ALLOWED_OPTION_KEYS)
    if unknown:
        raise ValidationError(f"Unknown import option(s): {', '.join(unknown)}.")
    return options


def summarize_errors(errors: list, limit: int = 5) -> str:
    """One readable sentence for the import endpoint's refusal: the first few problems with their row numbers."""
    def one(e):
        where = f"Row {e['row']}: " if e.get("row") else ""
        return where + e["message"]
    shown = "; ".join(one(e) for e in errors[:limit])
    more = len(errors) - limit
    return shown + (f" (and {more} more)" if more > 0 else "")
