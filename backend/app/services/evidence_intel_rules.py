"""Evidence intelligence rules: document metadata, review workflow, conflict detection and quality dimensions. Pure: no
database access.

Nothing here picks a preferred value. A conflict is reported with its sources, and only a person resolves it. Quality is
reported as separate dimensions with the facts behind each, never as one combined score."""
import hashlib
import json
from datetime import date

from backend.app.schemas.errors import ValidationError
from backend.app.services.evidence_rules import REPLICATE_CONFLICT_SPREAD_PCT

DOCUMENT_TYPES = {"test_report": "Test report", "lab_data": "Laboratory data", "supplier_datasheet": "Supplier datasheet",
                  "specification": "Specification", "literature": "Published literature", "internal_record": "Internal record",
                  "other": "Other"}
RELIABILITY = {"high": "High", "medium": "Medium", "low": "Low"}

ACCEPTED, REJECTED, CLARIFICATION, CONFLICT_FLAGGED, EXCLUDED, SUPERSEDED = (
    "accepted", "rejected", "clarification_requested", "conflict_flagged", "excluded", "superseded")
STATUS_LABELS = {ACCEPTED: "Accepted", REJECTED: "Rejected", CLARIFICATION: "Clarification requested",
                 CONFLICT_FLAGGED: "Conflict flagged", EXCLUDED: "Excluded", SUPERSEDED: "Superseded"}
HELD = (CLARIFICATION, CONFLICT_FLAGGED)       # under review: nothing newer may be ranked past it silently
OUT_OF_PLAY = (REJECTED, EXCLUDED, SUPERSEDED)  # not evidence any more, kept as history

ACTIONS = {"accept": (ACCEPTED, False), "reject": (REJECTED, True), "request_clarification": (CLARIFICATION, True),
           "flag_conflict": (CONFLICT_FLAGGED, True), "exclude": (EXCLUDED, True), "supersede": (SUPERSEDED, True)}
ACTION_LABELS = {"accept": "Accepted", "reject": "Rejected", "request_clarification": "Clarification requested",
                 "flag_conflict": "Conflict flagged", "exclude": "Excluded", "supersede": "Superseded"}

METADATA_LIMITS = {"document_type": 40, "source_note": 1000, "document_version": 60, "test_method": 200,
                   "document_date": 10, "source_reliability": 10}
METADATA_LABELS = {"document_type": "The document type", "source_note": "The source note", "document_version": "The version",
                   "test_method": "The test method", "document_date": "The document date", "source_reliability": "The reliability"}


def clean_metadata(changes: dict) -> dict:
    from backend.app.services import program_workspace_rules as w
    if not changes:
        raise ValidationError("Nothing to change.", field="changes")
    out = {}
    for key, value in changes.items():
        if key not in METADATA_LIMITS:
            raise ValidationError("That field cannot be changed.", field=key)
        if key == "document_date":
            out[key] = w.clean_date(value, key)
        else:
            out[key] = w.clean_text(value, key, METADATA_LIMITS[key], METADATA_LABELS[key])
        if key == "document_type" and out[key] is not None and out[key] not in DOCUMENT_TYPES:
            raise ValidationError("Choose a document type from the list.", field=key)
        if key == "source_reliability" and out[key] is not None and out[key] not in RELIABILITY:
            raise ValidationError("Choose high, medium or low.", field=key)
    return out


def check_review(action: str, from_status: str, reason, related_id, dataset_id: int):
    """-> (to_status, cleaned reason). Raises ValidationError when the move is not allowed."""
    from backend.app.services import program_workspace_rules as w
    if action not in ACTIONS:
        raise ValidationError("Choose a review action from the list.", field="action")
    to_status, needs_reason = ACTIONS[action]
    if from_status == SUPERSEDED:
        raise ValidationError("Superseded evidence is a historical record and cannot be reviewed again.", field="action")
    if from_status == to_status:
        raise ValidationError(f"This document is already {STATUS_LABELS[to_status].lower()}.", field="action")
    cleaned = w.clean_text(reason, "reason", 1000, "The reason", required=needs_reason or from_status in (REJECTED, EXCLUDED))
    if action == "supersede":
        if not related_id or related_id == dataset_id:
            raise ValidationError("Choose the newer document that replaces this one.", field="superseded_by")
    return to_status, cleaned


# ---------------------------------------------------------------- conflicts
def _fp(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _label(d: dict) -> str:
    return f"{d['filename']} (version {d['version_number']})"


def _in_play(datasets):
    return [d for d in datasets if d["status"] not in OUT_OF_PLAY]


def detect_conflicts(datasets: list) -> list:
    """Each dataset: {id, version_number, filename, status, sha, document_type, source_note, document_version, test_method,
    rows: [{id, features, target, source_row}], review: {units, conditions}}. Returns conflict dicts, none of which
    changes any data."""
    found = []

    def add(kind, severity, title, description, ids, details, key):
        found.append({"kind": kind, "severity": severity, "title": title, "description": description,
                      "dataset_ids": sorted(ids), "details": details, "fingerprint": _fp(kind, sorted(ids), key)})

    live = _in_play(datasets)
    by_id = {d["id"]: d for d in live}

    # 1 and 2: the same inputs measured in different documents, and exact repeats
    groups = {}
    for d in live:
        for r in d["rows"]:
            groups.setdefault(tuple(sorted(r["features"].items())), []).append((d["id"], r["id"], r["target"]))
    targets = [r["target"] for d in live for r in d["rows"]]
    overall_range = (max(targets) - min(targets)) if targets else 0.0
    pair_examples, pair_repeats = {}, {}
    for key, members in groups.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (da, ra, ta), (db, rb, tb) = members[i], members[j]
                if da == db:
                    continue
                pair = tuple(sorted((da, db)))
                if ta == tb:
                    pair_repeats.setdefault(pair, []).append({"inputs": dict(key), "value": ta, "rows": [ra, rb]})
                elif overall_range > 0 and abs(ta - tb) / overall_range > REPLICATE_CONFLICT_SPREAD_PCT:
                    pair_examples.setdefault(pair, []).append({"inputs": dict(key), "values": {str(da): ta, str(db): tb}, "rows": [ra, rb]})
    for pair, examples in pair_examples.items():
        a, b = by_id[pair[0]], by_id[pair[1]]
        add("conflicting_measurements", "high", "Conflicting measurements",
            f"{len(examples)} input set(s) have different results in {_label(a)} and {_label(b)}. The difference is more than "
            f"{REPLICATE_CONFLICT_SPREAD_PCT:.0%} of the range of all results. This threshold is a working convention, not a validated limit. "
            "No value has been chosen.", pair, {"examples": examples[:5], "total": len(examples)}, "measurements")
    for pair, examples in pair_repeats.items():
        a, b = by_id[pair[0]], by_id[pair[1]]
        add("duplicate_observations", "low", "Duplicate observations across documents",
            f"{len(examples)} observation(s) appear with identical inputs and results in {_label(a)} and {_label(b)}. "
            "They may be the same measurement reported twice.", pair, {"examples": examples[:5], "total": len(examples)}, "repeats")
    shas = {}
    for d in live:
        if d.get("sha"):
            shas.setdefault(d["sha"], []).append(d["id"])
    for sha, ids in shas.items():
        if len(ids) > 1:
            add("duplicate_document", "medium", "The same file was uploaded more than once",
                "These uploads have an identical file hash: " + ", ".join(_label(by_id[i]) for i in ids) + ".", ids, {"sha256": sha}, sha)
    for d in live:
        seen, dupes = set(), 0
        for r in d["rows"]:
            k = (tuple(sorted(r["features"].items())), r["target"])
            dupes += k in seen
            seen.add(k)
        if dupes:
            add("duplicate_observations", "low", "Repeated rows in one document",
                f"{dupes} row(s) in {_label(d)} repeat another row exactly.", [d["id"]], {"rows": dupes}, "within")

    # 3 and 4: conditions and methods that do not match
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            ca = (a["review"].get("conditions") or {}).get("reference") or {}
            cb = (b["review"].get("conditions") or {}).get("reference") or {}
            shared = sorted(set(ca) & set(cb))
            differing = {c: [ca[c], cb[c]] for c in shared if ca[c] != cb[c]}
            if differing:
                add("different_conditions", "medium", "Different test conditions",
                    f"{_label(a)} and {_label(b)} were measured under different conditions: "
                    + "; ".join(f"{c} {v[0]} and {v[1]}" for c, v in differing.items()) + ". Their results are not directly comparable.",
                    [a["id"], b["id"]], {"conditions": differing}, "conditions")
            elif bool(ca) != bool(cb):
                add("conditions_not_comparable", "low", "Test conditions are known for only one document",
                    f"Test conditions were declared for {_label(a if ca else b)} but not for {_label(b if ca else a)}, so the two cannot be compared.",
                    [a["id"], b["id"]], {}, "conditions_unknown")
            ma, mb = (a.get("test_method") or "").strip(), (b.get("test_method") or "").strip()
            if ma and mb and ma.lower() != mb.lower():
                add("incompatible_test_methods", "medium", "Different test methods",
                    f"{_label(a)} used {ma} and {_label(b)} used {mb}. Results from different methods are not assumed to be comparable.",
                    [a["id"], b["id"]], {"methods": [ma, mb]}, "methods")

    # 5, 6 and 7: units, missing metadata, suspicious or incomplete records (per document)
    for d in live:
        conversions = []
        for col, info in ((d["review"].get("units") or {}).items()):
            conversions += [f"{col}: {c['from']} to {c['to']} ({c['rows']} rows)" for c in (info or {}).get("conversions", [])]
        if conversions:
            add("unit_conversion", "low", "Units were converted",
                f"{_label(d)} reported some values in other units. They were converted with exact factors: " + "; ".join(conversions) + ".",
                [d["id"]], {"conversions": conversions}, "units")
        missing = [METADATA_LABELS[f][4:].lower() for f in ("document_type", "source_note", "document_version") if not d.get(f)]
        if missing:
            add("missing_metadata", "low", "Missing document details", f"{_label(d)} has no {', no '.join(missing)}.", [d["id"]],
                {"missing": missing}, "metadata")
        no_source = [r["id"] for r in d["rows"] if not r.get("source_row")]
        if no_source:
            add("incomplete_provenance", "medium", "Rows without a source location",
                f"{len(no_source)} row(s) of {_label(d)} have no recorded source location.", [d["id"]], {"rows": len(no_source)}, "provenance")
        if len(d["rows"]) >= 5 and len({r["target"] for r in d["rows"]}) == 1:
            add("suspicious_records", "medium", "Every result is identical",
                f"All {len(d['rows'])} results in {_label(d)} have the same value. Check the source.", [d["id"]], {"rows": len(d["rows"])}, "constant")
        if 0 < len(d["rows"]) < 5:
            add("suspicious_records", "low", "Very few rows",
                f"{_label(d)} has only {len(d['rows'])} row(s). Treat anything learned from it with caution.", [d["id"]], {"rows": len(d["rows"])}, "small")
    return found


# ---------------------------------------------------------------- quality dimensions
def quality_dimensions(d: dict, today: str) -> list:
    """Six separate, explained dimensions. Levels are 'not_assessed' when the system has no basis, and nothing is combined."""
    rows, review = d["rows"], d["review"]
    out = []

    def dim(key, label, level, basis, facts):
        out.append({"key": key, "label": label, "level": level, "basis": basis, "facts": facts})

    with_source = sum(1 for r in rows if r.get("source_row"))
    meta = ("document_type", "source_note", "document_version", "document_date")
    missing = [f for f in meta if not d.get(f)]
    level = "low" if rows and with_source < len(rows) else ("high" if not missing else "medium")
    dim("completeness", "Completeness", level,
        f"{with_source} of {len(rows)} rows have a source location. " + ("All document details are filled in." if not missing
            else "Missing document details: " + ", ".join(m.replace("_", " ") for m in missing) + "."),
        {"rows": len(rows), "rows_with_source": with_source, "missing_details": missing})

    rel = d.get("source_reliability")
    dim("source_reliability", "Source reliability", rel if rel in RELIABILITY else "not_assessed",
        "Set by a person who assessed the source." if rel in RELIABILITY
        else "Not assessed. The system cannot judge a source, so a reviewer has to set this.", {"set_by_reviewer": rel in RELIABILITY})

    unit_info = review.get("units") or {}
    converted = sum(c["rows"] for i in unit_info.values() for c in (i or {}).get("conversions", []))
    declared = [k for k, i in unit_info.items() if (i or {}).get("declared_source_unit")]
    undeclared = [k for k, i in unit_info.items() if (i or {}).get("canonical") is None]
    if not unit_info:
        dim("measurement_comparability", "Measurement comparability", "not_assessed", "No unit record exists for this upload.", {})
    else:
        level = "low" if undeclared else ("medium" if converted or declared else "high")
        dim("measurement_comparability", "Measurement comparability", level,
            ("Units were not declared for: " + ", ".join(undeclared) + ". " if undeclared else "")
            + (f"{converted} value(s) were converted with exact factors. " if converted else "")
            + ("A reviewer declared the unit for: " + ", ".join(declared) + ". " if declared else "")
            + ("All units were stated in the file and matched." if not (undeclared or converted or declared) else "")
            + (" Test method: " + d["test_method"] + "." if d.get("test_method") else " No test method is recorded."),
            {"rows_converted": converted, "declared_by_reviewer": declared, "units_undeclared": undeclared,
             "test_method": d.get("test_method")})

    if d.get("document_date"):
        try:
            months = int((date.fromisoformat(today) - date.fromisoformat(d["document_date"])).days / 30.4375)
        except ValueError:
            months = None
    else:
        months = None
    if months is None:
        dim("recency", "Recency", "not_assessed", "No document date is recorded.", {})
    else:
        level = "high" if months <= 24 else ("medium" if months <= 60 else "low")
        dim("recency", "Recency", level, f"The document is about {max(months, 0)} months old. The bands (up to 24 months, up to 60 months, "
            "older) are a working convention, not a validated standard.", {"age_months": max(months, 0), "document_date": d["document_date"]})

    groups = {}
    for r in rows:
        groups.setdefault(tuple(sorted(r["features"].items())), 0)
        groups[tuple(sorted(r["features"].items()))] += 1
    replicated_rows = sum(n for n in groups.values() if n > 1)
    share = replicated_rows / len(rows) if rows else 0
    dim("replication", "Replication", "none" if replicated_rows == 0 else ("substantial" if share >= 0.5 else "partial"),
        f"{replicated_rows} of {len(rows)} rows belong to an input set that was measured more than once.",
        {"rows": len(rows), "replicated_rows": replicated_rows, "replicated_input_sets": sum(1 for n in groups.values() if n > 1)})

    cond = review.get("conditions") or {}
    observed, reference = cond.get("observed") or {}, cond.get("reference") or {}
    if not observed and not reference:
        dim("test_condition_compatibility", "Test condition compatibility", "not_assessed",
            "No test condition columns were declared, so the conditions behind these values are unknown to the system.", {})
    else:
        dim("test_condition_compatibility", "Test condition compatibility", "high",
            "All rows used in the analysis were measured under one set of conditions: "
            + ", ".join(f"{c} {v}" for c, v in reference.items()) + ".", {"reference": reference})
    return out
