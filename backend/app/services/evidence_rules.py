"""
Evidence-intake rules (B2 column mapping + B3 units / conditions / conflicts).

Pure functions -- no database, no files, no framework imports -- so every rule
is directly unit-testable, in the same style as change_case_rules.py.

GOVERNING PRINCIPLE: if the system cannot establish that two values are
comparable, it DOES NOT GUESS. It raises a *blocking* flag and preserves the
context for a human. Only evidence that passes this gate reaches the model.

What is and is not automated:
- Column names: only an EXACT canonical header is accepted unaided. A
  case/punctuation-insensitive match, or a partial match, is only ever a
  PROPOSAL; it is used only if a person supplies it back as an explicit
  `column_mapping` (the confirmation). More than one candidate header is
  ambiguous and is never resolved by the software.
- Units: a value is converted ONLY when the change case declares a canonical
  unit for that column AND the two units are in the small table below with an
  exact conversion factor. Anything else (unknown unit, different dimension,
  e.g. hours vs percent) is blocked, never converted.
- Test conditions: declared condition columns are never collapsed. Rows
  measured under different conditions are blocked until a person names ONE
  reference condition set (the others are then excluded and recorded).
- Conflicts: identical inputs with materially different targets are blocked
  until a person fixes the source or explicitly excludes rows with a reason.
"""
import hashlib
import math
import re

# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------
BLOCKING = "blocking"
WARNING = "warning"

# HEURISTIC, NOT SCIENTIFICALLY VALIDATED (same status as DOMAIN_EDGE_MARGIN_PCT
# in change_case_rules.py). Identical inputs with differing targets are normal
# replicate scatter up to some size; beyond this fraction of the dataset's
# overall target range they are treated as a CONFLICT needing human review.
# Below it they are only reported (REPLICATE_VARIATION), never hidden.
REPLICATE_CONFLICT_SPREAD_PCT = 0.10

MAX_ROWS_LISTED = 50  # bound the size of row lists stored in a review record

ALLOWED_OPTION_KEYS = {
    "column_mapping", "declared_units", "condition_columns",
    "reference_conditions", "exclude_rows", "exclusion_reason",
    "table_ref", "header_row",
}


def _flag(severity, code, message, rows=None, column=None):
    f = {"severity": severity, "code": code, "message": message}
    if column is not None:
        f["column"] = column
    if rows:
        f["rows"] = sorted(rows)[:MAX_ROWS_LISTED]
        if len(rows) > MAX_ROWS_LISTED:
            f["rows_truncated"] = True
    return f


make_flag = _flag  # public name for other modules (evidence_extraction)


def blocked_review(flags):
    """A standard-shaped review record for a file that cannot even be turned
    into rows yet (e.g. no table selected). Carries only flags; no rows."""
    return {
        "version": 1, "column_mapping": {}, "proposed_mapping": {}, "proposed_units": {},
        "proposed_condition_columns": [], "ignored_columns": [], "provenance_columns": [],
        "condition_columns": [], "units": {}, "conditions": {}, "exclusions": {}, "replicates": {},
        "counts": {}, "flags": list(flags),
    }


def blocking_flags(flags):
    return [f for f in flags if f["severity"] == BLOCKING]


def summarize_blocking(flags, limit=6):
    """One safe, human-readable message (shown to the customer/operator)."""
    blocking = blocking_flags(flags)
    parts = []
    for f in blocking[:limit]:
        rows = f" (data rows {', '.join(str(r) for r in f['rows'][:10])})" if f.get("rows") else ""
        parts.append(f"[{f['code']}] {f['message']}{rows}")
    more = f" ...and {len(blocking) - limit} more." if len(blocking) > limit else ""
    return (
        f"Dataset not accepted: {len(blocking)} issue(s) need review before any data reaches "
        "the model. " + " ".join(parts) + more +
        " Use the dataset preview to see the full review."
    )


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# token -> (dimension, factor to that dimension's base unit). Every factor is
# exact by definition. A unit not in this table is "opaque": it is only ever
# equal to the identical (normalised) token, never converted.
_UNIT_TABLE = {
    # time (base: second)
    "s": ("time", 1.0), "sec": ("time", 1.0),
    "min": ("time", 60.0),
    "h": ("time", 3600.0), "hr": ("time", 3600.0), "hour": ("time", 3600.0), "hours": ("time", 3600.0),
    "d": ("time", 86400.0), "day": ("time", 86400.0), "days": ("time", 86400.0),
    # length (base: metre)
    "m": ("length", 1.0), "cm": ("length", 0.01), "mm": ("length", 0.001),
    "um": ("length", 1e-6), "micron": ("length", 1e-6), "microns": ("length", 1e-6),
    "nm": ("length", 1e-9), "mil": ("length", 2.54e-5),
    # dynamic viscosity (base: mPa.s; 1 cP == 1 mPa.s exactly)
    "mpa.s": ("viscosity", 1.0), "cp": ("viscosity", 1.0), "centipoise": ("viscosity", 1.0),
    "pa.s": ("viscosity", 1000.0), "p": ("viscosity", 100.0), "poise": ("viscosity", 100.0),
    # temperature: identity only (offset scales are deliberately not converted)
    "degc": ("temperature_c", 1.0), "c": ("temperature_c", 1.0),
}


def normalize_unit(unit):
    """Canonical comparison token for a unit string (None stays None)."""
    if unit is None:
        return None
    u = str(unit).strip().lower()
    u = u.replace("°", "deg").replace("µ", "u").replace("μ", "u").replace("·", ".").replace("*", ".")
    u = re.sub(r"\s+", "", u)
    return u or None


def convert_value(value, from_unit, to_unit):
    """Returns (ok, converted_value). ok is True only when the units are
    identical after normalisation, or both are in _UNIT_TABLE with the same
    dimension. Never guesses."""
    f, t = normalize_unit(from_unit), normalize_unit(to_unit)
    if f is None or t is None:
        return False, None
    if f == t:
        return True, value
    if f in _UNIT_TABLE and t in _UNIT_TABLE:
        fd, ff = _UNIT_TABLE[f]
        td, tf = _UNIT_TABLE[t]
        if fd == td:
            # 12 significant digits removes binary-float noise (e.g. 25.400000000000002)
            # so equal quantities stay exactly equal for duplicate/conflict checks.
            return True, float(f"{value * ff / tf:.12g}")
    return False, None


def units_convertible(a, b):
    ok, _ = convert_value(1.0, a, b)
    return ok


# ---------------------------------------------------------------------------
# Cell parsing
# ---------------------------------------------------------------------------
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_QTY = re.compile(rf"^\s*({_NUM})\s*([^\d\s,;+\-.][A-Za-z0-9°µμ%/·.\-^*]{{0,14}})\s*$")
_AMBIGUOUS_COMMA = re.compile(r"^\s*[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*$|^\s*[+-]?\d+,\d+\s*$")

PARSE_OK = "ok"
PARSE_AMBIGUOUS = "ambiguous_number"
PARSE_NONE = "not_a_number"


def parse_quantity(cell):
    """-> (status, value, unit).
    status PARSE_OK: value is a float (may be non-finite, caller checks), unit is
    the raw unit text or None. PARSE_AMBIGUOUS: a comma number such as '0,085'
    or '1,234' -- decimal vs thousands separator cannot be told apart, so it is
    never interpreted. PARSE_NONE: not a number."""
    if cell is None:
        return PARSE_NONE, None, None
    s = str(cell).strip()
    if not s:
        return PARSE_NONE, None, None
    try:
        return PARSE_OK, float(s), None
    except ValueError:
        pass
    if _AMBIGUOUS_COMMA.match(s):
        return PARSE_AMBIGUOUS, None, None
    m = _QTY.match(s)
    if m:
        try:
            return PARSE_OK, float(m.group(1)), m.group(2)
        except ValueError:
            return PARSE_NONE, None, None
    return PARSE_NONE, None, None


# ---------------------------------------------------------------------------
# Header matching (B2)
# ---------------------------------------------------------------------------
def _header_key(header):
    """Lower-case words with punctuation/underscores collapsed and any
    trailing '(unit)' / '[unit]' removed. Returns (key, unit_hint)."""
    h = str(header or "").strip()
    hint = None
    m = re.search(r"[\(\[]([^\)\]]*)[\)\]]\s*$", h)
    if m:
        hint = m.group(1).strip() or None  # original case kept: it is shown to the reviewer
        h = h[: m.start()]
    h = h.lower()
    h = re.sub(r"[_\-\./]+", " ", h)
    h = re.sub(r"\s+", " ", h).strip()
    return h, hint


def propose_header_matches(canonical, headers):
    """Candidate source headers for ONE canonical column, strongest first.
    Nothing here is ever applied automatically.
    -> list of {"header", "confidence": "exact"|"alias"|"partial"}"""
    if canonical in headers:
        return [{"header": canonical, "confidence": "exact"}]
    ckey, _ = _header_key(canonical)
    ctokens = set(ckey.split())
    alias, partial = [], []
    for h in headers:
        hkey, _ = _header_key(h)
        if not hkey:
            continue
        if hkey == ckey:
            alias.append({"header": h, "confidence": "alias"})
            continue
        htokens = set(hkey.split())
        if htokens and ctokens and (htokens <= ctokens or ctokens <= htokens):
            partial.append({"header": h, "confidence": "partial"})
    return alias + partial


_SUSPECT_IGNORED = re.compile(r"unit|uom|method|standard|astm|iso|temp|humid|condition|substrate|panel|test", re.I)
_PROVENANCE_HEADERS = {"source", "source_document", "source_doc", "source_page", "source_table", "source_file", "document", "page", "table"}


def classify_extra_columns(headers, used_headers, condition_headers):
    """-> (ignored, provenance, suspect). Extra columns are never merged into
    the dataset; provenance columns are recognised by name; ignored columns
    whose names look like unit / method / condition columns are returned in
    `suspect` so they cannot disappear silently."""
    ignored, provenance, suspect = [], [], []
    for h in headers:
        if h in used_headers or h in condition_headers:
            continue
        if not str(h).strip():
            continue
        if _header_key(h)[0].replace(" ", "_") in _PROVENANCE_HEADERS:
            provenance.append(h)
            continue
        ignored.append(h)
        if _SUSPECT_IGNORED.search(str(h)):
            suspect.append(h)
    return ignored, provenance, suspect


def resolve_columns(required, headers, column_mapping):
    """required: canonical column names. headers: source header row.
    column_mapping: {canonical: source_header} supplied by a person (or {}).
    -> (resolved {canonical: {"source_header", "method"}}, proposals, flags).
    Only an exact canonical header, or an explicit mapping entry, resolves."""
    flags = []
    resolved = {}
    proposals = {}
    header_counts = {}
    for h in headers:
        header_counts[h] = header_counts.get(h, 0) + 1
    for h, n in header_counts.items():
        if n > 1 and str(h).strip():
            flags.append(_flag(BLOCKING, "DUPLICATE_HEADER",
                               f"Column header {h!r} appears {n} times; which one is meant cannot be determined.",
                               column=h))
    mapping = dict(column_mapping or {})
    for canon in mapping:
        if canon not in required:
            flags.append(_flag(BLOCKING, "INVALID_MAPPING",
                               f"Mapping names {canon!r}, which is not a required column of this change case.",
                               column=canon))
    used = {}
    for canon in required:
        if canon in mapping:
            src = mapping[canon]
            if src not in headers:
                flags.append(_flag(BLOCKING, "INVALID_MAPPING",
                                   f"Mapping for {canon!r} points to {src!r}, which is not a header in the file.",
                                   column=canon))
                continue
            resolved[canon] = {"source_header": src, "method": "confirmed_mapping"}
        elif canon in headers:
            resolved[canon] = {"source_header": canon, "method": "exact"}
        else:
            cands = propose_header_matches(canon, headers)
            proposals[canon] = cands
            if not cands:
                flags.append(_flag(BLOCKING, "MISSING_REQUIRED_COLUMN",
                                   f"Required column {canon!r} is not in the file and nothing resembles it.",
                                   column=canon))
            elif len(cands) == 1:
                c = cands[0]
                flags.append(_flag(BLOCKING, "MAPPING_NOT_CONFIRMED",
                                   f"Required column {canon!r} is not in the file. Proposed match: {c['header']!r} "
                                   f"({c['confidence']}). Confirm it by supplying column_mapping "
                                   f"{{{canon!r}: {c['header']!r}}}.",
                                   column=canon))
            else:
                names = ", ".join(repr(c["header"]) for c in cands)
                flags.append(_flag(BLOCKING, "AMBIGUOUS_COLUMN_MAPPING",
                                   f"Required column {canon!r} could be any of: {names}. "
                                   "The software does not choose; supply column_mapping explicitly.",
                                   column=canon))
    for canon, info in resolved.items():
        used.setdefault(info["source_header"], []).append(canon)
    for src, canons in used.items():
        if len(canons) > 1:
            flags.append(_flag(BLOCKING, "INVALID_MAPPING",
                               f"Header {src!r} is mapped to more than one required column: {', '.join(sorted(canons))}."))
    return resolved, proposals, flags


# ---------------------------------------------------------------------------
# Options validation
# ---------------------------------------------------------------------------
def validate_options(options):
    """-> (clean_options, flags). Unknown keys are rejected so a typo cannot
    silently turn a safeguard off."""
    flags = []
    if options is None:
        return {}, flags
    if not isinstance(options, dict):
        return {}, [_flag(BLOCKING, "INVALID_OPTIONS", "Options must be a JSON object.")]
    for k in options:
        if k not in ALLOWED_OPTION_KEYS:
            flags.append(_flag(BLOCKING, "INVALID_OPTIONS",
                               f"Unknown option {k!r}. Allowed: {', '.join(sorted(ALLOWED_OPTION_KEYS))}."))
    def _str_dict(name):
        v = options.get(name) or {}
        if not isinstance(v, dict) or not all(isinstance(a, str) and isinstance(b, str) for a, b in v.items()):
            flags.append(_flag(BLOCKING, "INVALID_OPTIONS", f"Option {name!r} must map text to text."))
            return {}
        return v
    clean = {
        "column_mapping": _str_dict("column_mapping"),
        "declared_units": _str_dict("declared_units"),
        "reference_conditions": _str_dict("reference_conditions"),
    }
    cc = options.get("condition_columns") or []
    if not isinstance(cc, list) or not all(isinstance(c, str) for c in cc):
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", "Option 'condition_columns' must be a list of header names."))
        cc = []
    clean["condition_columns"] = cc
    ex = options.get("exclude_rows") or []
    if not isinstance(ex, list) or not all(isinstance(r, int) and not isinstance(r, bool) and r >= 1 for r in ex):
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", "Option 'exclude_rows' must be a list of data-row numbers (1 = first data row)."))
        ex = []
    clean["exclude_rows"] = sorted(set(ex))
    reason = options.get("exclusion_reason")
    if reason is not None and not isinstance(reason, str):
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", "Option 'exclusion_reason' must be text."))
        reason = None
    clean["exclusion_reason"] = (reason or "").strip()
    tr = options.get("table_ref")
    if tr is not None and not (isinstance(tr, str) or (isinstance(tr, list) and all(isinstance(x, str) for x in tr))):
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", "Option 'table_ref' must be text or a list of text."))
        tr = None
    clean["table_ref"] = tr
    hr = options.get("header_row")
    if hr is not None and (not isinstance(hr, int) or isinstance(hr, bool) or hr < 1):
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", "Option 'header_row' must be a source row number (1 or more)."))
        hr = None
    clean["header_row"] = hr
    if clean["exclude_rows"] and not clean["exclusion_reason"]:
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS",
                           "Excluding rows requires an 'exclusion_reason' so the exclusion is auditable."))
    if clean["reference_conditions"] and not cc:
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", "'reference_conditions' requires 'condition_columns'."))
    return clean, flags


def validate_spec_extensions(spec):
    """Checks the OPTIONAL spec keys this module adds ('units'). Returns an
    error message or None. Absent keys are always fine (back-compatible)."""
    units = spec.get("units")
    if units is None:
        return None
    if not isinstance(units, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in units.items()):
        return "qualification_spec 'units' must map column names to non-empty unit text."
    known = set(spec.get("feature_columns") or []) | {spec.get("target_metric")}
    unknown = sorted(set(units) - known)
    if unknown:
        return f"qualification_spec 'units' names columns that are not features or the target: {', '.join(unknown)}."
    return None


# ---------------------------------------------------------------------------
# Main gate
# ---------------------------------------------------------------------------
def _condition_key(cell):
    """-> (key, display). `key` decides whether two condition values are the
    SAME condition: numbers compare numerically and, when the unit is in the
    exact-conversion table, in that dimension's base unit (so '25 C' == '25.0 degC'
    and '1 day' == '24 h'); an unknown unit only matches itself ('25 K' is NOT
    '25 C'). `display` is the first-seen human spelling, used in reports."""
    status, value, unit = parse_quantity(cell)
    if status == PARSE_OK and value is not None and math.isfinite(value):
        u = normalize_unit(unit)
        display = f"{value:g}" + (f" {unit.strip()}" if unit and unit.strip() else "")
        if u in _UNIT_TABLE:
            dim, factor = _UNIT_TABLE[u]
            return f"{dim}:{float(f'{value * factor:.12g}')!r}", display
        return f"{value:g}" + (f" {u}" if u else ""), display
    text = re.sub(r"\s+", " ", str(cell).strip().lower())
    return text, str(cell).strip()


def analyze_rows(spec, headers, data_rows, options, row_refs=None):
    """Public entry: runs the B2/B3 gate (see _analyze_rows_impl) and, when
    `row_refs` (one source reference per data row) is given, attaches source
    provenance to every accepted row and a human-readable `row_sources` list
    to every finding that names rows."""
    review, rows_out, errors = _analyze_rows_impl(spec, headers, data_rows, options, row_refs)
    if row_refs:
        def src(i):
            ref = row_refs[i - 1]
            return f"{ref.get('location') or ref.get('table')} row {ref.get('row')}"
        n = len(row_refs)
        for f in review["flags"]:
            if f.get("rows"):
                f["row_sources"] = [src(r) for r in f["rows"] if 1 <= r <= n]
        for e in errors:
            if 1 <= e.get("row", 0) <= n:
                e["source"] = src(e["row"])
    return review, rows_out, errors


def _analyze_rows_impl(spec, headers, data_rows, options, row_refs=None):
    """Runs the whole B2/B3 gate over already-decoded rows.

    headers: list[str]; data_rows: list[dict header->cell string] in file order.
    Returns (review, rows_out, errors). `rows_out` is the ONLY evidence that may
    be stored / modelled: canonical column names, canonical units. If the review
    contains any blocking flag the caller must refuse the upload."""
    feature_columns = list(spec["feature_columns"])
    target = spec["target_metric"]
    required = feature_columns + [target]
    spec_units = spec.get("units") or {}

    opts, flags = validate_options(options)
    resolved, proposals, res_flags = resolve_columns(required, headers, opts.get("column_mapping"))
    flags += res_flags

    cond_cols = opts.get("condition_columns", [])
    for c in cond_cols:
        if c not in headers:
            flags.append(_flag(BLOCKING, "INVALID_OPTIONS", f"Condition column {c!r} is not a header in the file.", column=c))
    cond_cols = [c for c in cond_cols if c in headers]
    used_headers = {i["source_header"] for i in resolved.values()}
    overlap = [c for c in cond_cols if c in used_headers]
    for c in overlap:
        flags.append(_flag(BLOCKING, "INVALID_OPTIONS", f"{c!r} is both a modelled column and a condition column.", column=c))

    ignored, provenance, suspect = classify_extra_columns(headers, used_headers, set(cond_cols))
    for h in ignored:
        if h in suspect:
            flags.append(_flag(WARNING, "POSSIBLE_UNIT_OR_CONDITION_COLUMN_IGNORED",
                               f"Column {h!r} is not used and its name suggests a unit, method or test condition. "
                               "If it affects how values compare, declare it (condition_columns / declared_units) "
                               "and re-run the review.", column=h))
    if ignored:
        flags.append(_flag(WARNING, "COLUMNS_IGNORED",
                           "Columns not used by the model (preserved only in the stored file): " + ", ".join(repr(h) for h in ignored) + ".",
                           ))
    if not provenance:
        flags.append(_flag(WARNING, "NO_PROVENANCE_COLUMNS",
                           "The file has no source_document / source_page / source_table columns, so the stored file "
                           "does not itself show where each value came from."))

    review = {
        "version": 1,
        "column_mapping": {c: dict(i) for c, i in resolved.items()},
        "proposed_mapping": {c: p[0]["header"] for c, p in proposals.items() if len(p) == 1},
        # Display-only unit hints taken from "(unit)" in the header text -- for the
        # confirmed mapping AND for headers merely proposed, so the reviewer sees
        # them next to the proposal. Never applied: units must be declared.
        "proposed_units": {
            c: hint
            for c, header in (
                {**{c: i["source_header"] for c, i in resolved.items()},
                 **{c: p[0]["header"] for c, p in proposals.items() if len(p) == 1}}
            ).items()
            for hint in [_header_key(header)[1]] if hint
        },
        "proposed_condition_columns": [h for h in ignored if h in suspect],
        "ignored_columns": ignored,
        "provenance_columns": provenance,
        "condition_columns": cond_cols,
        "units": {},
        "conditions": {},
        "exclusions": {},
        "replicates": {},
        "counts": {},
        "flags": flags,
    }

    # Cannot go further without every required column resolved.
    if any(c not in resolved for c in required) or blocking_flags([f for f in flags if f["code"] in ("DUPLICATE_HEADER", "INVALID_MAPPING", "INVALID_OPTIONS")]):
        review["counts"] = {"data_rows": len(data_rows)}
        return review, [], []

    # ---- pass 1: parse every required cell ---------------------------------
    parsed = []          # dicts: row, cells{canon:(value,unit)}, cond(tuple)
    errors = []
    blank = 0
    excluded_by_operator = []
    ambiguous_rows = []
    cond_missing_rows = []
    exclude_set = set(opts.get("exclude_rows", []))
    cond_display = {}
    for idx, raw in enumerate(data_rows, start=1):
        if all((str(v).strip() == "" if v is not None else True) for v in raw.values()):
            blank += 1
            continue
        if idx in exclude_set:
            excluded_by_operator.append(idx)
            continue
        cells = {}
        bad = None
        for canon in required:
            status, value, unit = parse_quantity(raw.get(resolved[canon]["source_header"]))
            if status == PARSE_AMBIGUOUS:
                ambiguous_rows.append(idx)
                bad = "ambiguous"
                break
            if status != PARSE_OK:
                bad = "non_numeric"
                break
            if not math.isfinite(value):
                bad = "non_finite"
                break
            cells[canon] = (value, unit)
        if bad == "ambiguous":
            continue
        if bad == "non_numeric":
            errors.append({"row": idx, "error": "Non-numeric value in a required column."})
            continue
        if bad == "non_finite":
            errors.append({"row": idx, "error": "Non-finite value (NaN or Infinity) in a required column."})
            continue
        cond = None
        if cond_cols:
            parts = [str(raw.get(c) if raw.get(c) is not None else "").strip() for c in cond_cols]
            if any(p == "" for p in parts):
                cond_missing_rows.append(idx)
                continue
            keyed = [_condition_key(p) for p in parts]
            cond = tuple(k for k, _ in keyed)
            for k, d in keyed:
                cond_display.setdefault(k, d)
        parsed.append({"row": idx, "cells": cells, "cond": cond})

    if ambiguous_rows:
        flags.append(_flag(BLOCKING, "AMBIGUOUS_NUMBER",
                           "A required value contains a comma (e.g. '0,085' or '1,234'); whether the comma is a decimal "
                           "or a thousands separator cannot be determined. Correct it in the source.", rows=ambiguous_rows))
    if cond_missing_rows:
        flags.append(_flag(BLOCKING, "CONDITION_MISSING",
                           "A declared test-condition value is blank, so the row cannot be assigned to a condition set.",
                           rows=cond_missing_rows))
    if excluded_by_operator:
        flags.append(_flag(WARNING, "ROWS_EXCLUDED_BY_OPERATOR",
                           f"{len(excluded_by_operator)} row(s) excluded by the reviewer. Reason: {opts['exclusion_reason']}",
                           rows=excluded_by_operator))
        review["exclusions"]["operator_excluded_rows"] = excluded_by_operator[:MAX_ROWS_LISTED]
        review["exclusions"]["reason"] = opts["exclusion_reason"]
    if errors:
        flags.append(_flag(WARNING, "ROWS_SKIPPED_UNPARSEABLE",
                           f"{len(errors)} row(s) skipped: a required value was not a usable number.",
                           rows=[e["row"] for e in errors]))

    # ---- units, column by column -------------------------------------------
    declared = opts.get("declared_units", {})
    for canon in required:
        E = spec_units.get(canon)
        S = declared.get(canon)
        rows_no_unit, seen = [], {}
        for p in parsed:
            u = p["cells"][canon][1] or S
            if u is None:
                rows_no_unit.append(p["row"])
            else:
                seen.setdefault(normalize_unit(u), []).append(p["row"])
        info = {"canonical": E, "declared_source_unit": S,
                "source_units_seen": sorted(k for k in seen if k), "rows_converted": 0, "conversions": []}
        if E is None:
            if not seen:
                flags.append(_flag(WARNING, "UNITS_NOT_DECLARED",
                                   f"No unit is declared or present for {canon!r}; values are used as supplied and "
                                   "their unit is unknown to the system.", column=canon))
            elif rows_no_unit:
                flags.append(_flag(BLOCKING, "UNIT_MISSING",
                                   f"{canon!r}: some rows carry a unit and others do not; the unitless rows cannot be "
                                   "shown to be comparable.", rows=rows_no_unit, column=canon))
            elif len(seen) > 1:
                desc = ", ".join(f"{u} ({len(r)} rows)" for u, r in sorted(seen.items()))
                conv = all(units_convertible(a, b) for a in seen for b in seen)
                hint = (" These units are interconvertible: declare the canonical unit in the change case's "
                        "qualification_spec 'units' to have them converted explicitly, or fix the source."
                        if conv else " These units are not interconvertible; the source must be corrected.")
                flags.append(_flag(BLOCKING, "UNIT_MIXED",
                                   f"{canon!r} mixes units: {desc}.{hint}", column=canon))
            else:
                info["canonical"] = next(iter(seen))
        else:
            if rows_no_unit:
                flags.append(_flag(BLOCKING, "UNIT_MISSING",
                                   f"{canon!r} must be in {E!r} but these rows have no unit and none was declared "
                                   "for the column (declared_units).", rows=rows_no_unit, column=canon))
            bad_units = {u: r for u, r in seen.items() if not units_convertible(u, E)}
            for u, r in sorted(bad_units.items()):
                flags.append(_flag(BLOCKING, "UNIT_INCOMPATIBLE",
                                   f"{canon!r} must be in {E!r}; unit {u!r} cannot be safely converted to it.",
                                   rows=r, column=canon))
            if not rows_no_unit and not bad_units:
                for u, r in sorted(seen.items()):
                    if normalize_unit(u) != normalize_unit(E):
                        info["conversions"].append({"from": u, "to": E, "rows": len(r)})
                        info["rows_converted"] += len(r)
                        flags.append(_flag(WARNING, "UNIT_CONVERTED",
                                           f"{canon!r}: {len(r)} row(s) converted from {u!r} to {E!r} using an exact "
                                           "conversion factor.", rows=r, column=canon))
        review["units"][canon] = info

    if blocking_flags(flags):
        review["counts"] = {"data_rows": len(data_rows), "blank_rows_ignored": blank}
        return review, [], errors

    # ---- apply conversions ---------------------------------------------------
    for p in parsed:
        for canon in required:
            value, cell_unit = p["cells"][canon]
            E = spec_units.get(canon)
            u = cell_unit or declared.get(canon)
            if E is not None and u is not None:
                _, value = convert_value(value, u, E)
            p["cells"][canon] = (value, None)

    # ---- test conditions -------------------------------------------------------
    excluded_other_conditions = 0
    if cond_cols:
        observed = sorted({p["cond"] for p in parsed})
        review["conditions"]["observed"] = {
            c: sorted({cond_display[p["cond"][i]] for p in parsed}) for i, c in enumerate(cond_cols)
        }
        ref_raw = opts.get("reference_conditions") or {}
        if ref_raw:
            missing_ref = [c for c in cond_cols if c not in ref_raw]
            if missing_ref:
                flags.append(_flag(BLOCKING, "INVALID_OPTIONS",
                                   "reference_conditions must give a value for every condition column; missing: "
                                   + ", ".join(repr(c) for c in missing_ref)))
            else:
                ref_key = tuple(_condition_key(ref_raw[c])[0] for c in cond_cols)
                if ref_key not in observed:
                    flags.append(_flag(BLOCKING, "INVALID_OPTIONS",
                                       "reference_conditions matches no row in the file."))
                else:
                    kept = [p for p in parsed if p["cond"] == ref_key]
                    excluded_other_conditions = len(parsed) - len(kept)
                    parsed = kept
                    review["conditions"]["reference"] = {c: cond_display[ref_key[i]] for i, c in enumerate(cond_cols)}
                    review["conditions"]["rows_excluded_other_conditions"] = excluded_other_conditions
                    if excluded_other_conditions:
                        flags.append(_flag(WARNING, "ROWS_EXCLUDED_OTHER_CONDITIONS",
                                           f"{excluded_other_conditions} row(s) measured under other test conditions were "
                                           "excluded; the model sees only the reference condition set."))
        elif len(observed) > 1:
            desc = "; ".join(", ".join(f"{c}={cond_display[k[i]]}" for i, c in enumerate(cond_cols)) for k in observed)
            flags.append(_flag(BLOCKING, "CONDITIONS_DIFFER",
                               f"Rows were measured under different test conditions ({desc}). These are not the same "
                               "observation and the model cannot represent conditions. Name ONE reference condition set "
                               "(reference_conditions) or split the data; the software will not merge them."))
        else:
            review["conditions"]["reference"] = (
                {c: cond_display[observed[0][i]] for i, c in enumerate(cond_cols)} if observed else {}
            )

    if blocking_flags(flags):
        review["counts"] = {"data_rows": len(data_rows), "blank_rows_ignored": blank}
        return review, [], errors

    # ---- conflicting observations -------------------------------------------
    rows_out = []
    groups = {}
    for p in parsed:
        features = {c: p["cells"][c][0] for c in feature_columns}
        target_value = p["cells"][target][0]
        rows_out.append({"features": features, "target_value": target_value, "_row": p["row"]})
        groups.setdefault(tuple(sorted(features.items())), []).append((p["row"], target_value))
    targets = [r["target_value"] for r in rows_out]
    overall_range = (max(targets) - min(targets)) if targets else 0.0
    conflicts, varied, max_rel = [], 0, 0.0
    for key, members in groups.items():
        vals = [t for _, t in members]
        if len(set(vals)) > 1 and overall_range > 0:
            rel = (max(vals) - min(vals)) / overall_range
            max_rel = max(max_rel, rel)
            varied += 1
            if rel > REPLICATE_CONFLICT_SPREAD_PCT:
                conflicts.append((dict(key), members, rel))
    for feats, members, rel in conflicts[:5]:
        flags.append(_flag(BLOCKING, "CONFLICTING_OBSERVATIONS",
                           f"Identical inputs {feats} have targets {sorted(set(t for _, t in members))} "
                           f"(spread {rel:.0%} of the dataset's target range, above the {REPLICATE_CONFLICT_SPREAD_PCT:.0%} "
                           "review threshold). Fix the source or exclude rows explicitly with a reason.",
                           rows=[r for r, _ in members]))
    if len(conflicts) > 5:
        flags.append(_flag(BLOCKING, "CONFLICTING_OBSERVATIONS", f"{len(conflicts) - 5} further conflicting input group(s)."))
    if varied and not conflicts:
        flags.append(_flag(WARNING, "REPLICATE_VARIATION",
                           f"{varied} input set(s) have replicate rows with differing targets (largest spread "
                           f"{max_rel:.0%} of the target range); reported, not hidden."))
    review["replicates"] = {"groups_with_differing_targets": varied, "max_spread_fraction_of_range": round(max_rel, 4),
                            "conflict_threshold_fraction": REPLICATE_CONFLICT_SPREAD_PCT,
                            "threshold_status": "HEURISTIC, NOT SCIENTIFICALLY VALIDATED"}

    review["counts"] = {"data_rows": len(data_rows), "blank_rows_ignored": blank, "ingested": len(rows_out),
                        "skipped_unparseable": len(errors), "excluded_by_reviewer": len(excluded_by_operator),
                        "excluded_other_conditions": excluded_other_conditions}
    for r in rows_out:
        ri = r.pop("_row", None)
        if row_refs and ri:
            r["provenance"] = row_refs[ri - 1]
    if blocking_flags(flags):
        return review, [], errors
    return review, rows_out, errors


def file_sha256(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def convert_candidate_inputs(spec, features, units):
    """Canonical-unit discipline for candidate inputs, same rules as the
    historical data: where the case declares a canonical unit for a feature
    the candidate MUST state a unit and it is converted only by an exact
    factor; a unit the case cannot check is refused; nothing is guessed.

    -> (features_in_canonical_units, input_record, problems)
    `input_record` keeps what was entered (value + unit) and what it became,
    so the audit trail never loses the original."""
    spec_units = spec.get("units") or {}
    units = units or {}
    columns = list(spec.get("feature_columns") or [])
    converted = dict(features)
    record, problems = {}, []
    for col in columns:
        if col not in features:
            continue
        value = features[col]
        raw_unit = units.get(col)
        unit = raw_unit.strip() if isinstance(raw_unit, str) and raw_unit.strip() else None
        entry = {"value": value, "unit": unit}
        canonical = spec_units.get(col)
        if canonical is None:
            if unit is not None:
                problems.append(
                    f"{col}: this case has no canonical unit for {col!r}, so the unit {unit!r} cannot be checked "
                    "against the historical data. Remove the unit, or declare the case's unit first."
                )
        elif unit is None:
            problems.append(f"{col}: a unit is required -- this case uses {canonical!r}.")
        else:
            ok, v = convert_value(value, unit, canonical)
            if not ok:
                problems.append(f"{col}: unit {unit!r} cannot be safely converted to this case's unit {canonical!r}.")
            else:
                converted[col] = v
                entry["converted_value"] = v
                entry["converted_unit"] = canonical
        record[col] = entry
    for k in units:
        if k not in columns:
            problems.append(f"A unit was given for {k!r}, which is not a feature of this case.")
    return converted, record, problems
