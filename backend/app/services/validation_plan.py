"""Facts for the report's physical-validation plan. Pure functions: no database, no framework.

Everything here is taken from records the system already holds (the requirement, the candidate's inputs, the
historical evidence rows, the per-feature range check). Nothing is invented: no test method, specimen count,
acceptance limit or result. The only acceptance criterion the system holds is the single threshold in the spec."""
import math

CLOSEST_RECORDS_SHOWN = 3

CLOSEST_RECORDS_NOTE = (
    "Closest historical records: the recorded measurements whose inputs are nearest the candidate's, by equal-weight "
    "distance over every modelled input scaled to its historical range. This ordering is a display aid, not a "
    "validated similarity measure, and a near record does not show that the candidate is covered by the evidence."
)

NOT_SPECIFIED_BY_ANALYSIS = (
    "This analysis does not specify the test method or standard, the specimen type, the number of specimens or "
    "repeats, or any acceptance rule other than the single threshold above. It holds no other requirement for "
    "this candidate. Those come from the customer's own qualification procedure."
)


def record_reference(record_id, provenance) -> str:
    """'evidence record 12 (history.csv row 7)' -- the stored row id plus the source reference when one was kept."""
    ref = f"evidence record {record_id}"
    if isinstance(provenance, dict):
        where = provenance.get("location") or provenance.get("table") or provenance.get("file")
        row = provenance.get("row")
        if where and row is not None:
            ref += f" ({where} row {row})"
        elif where:
            ref += f" ({where})"
    return ref


def _is_finite_number(value) -> bool:
    """A real, finite int or float. Rejects bool, NaN, +/-infinity, and integers too large to be a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int beyond float range: finite in principle, unusable as a distance input
        return False


def closest_records(candidate_features: dict, records: list, feature_columns: list, k: int = CLOSEST_RECORDS_SHOWN) -> list:
    """The k historical records nearest the candidate. `records`: dicts with id, features, target_value, provenance
    (the shape of the training rows; the caller removes exact duplicates with the training deduplication).

    Equal-weight Euclidean distance with each feature scaled by its historical (max - min); a feature with no
    spread contributes 0. Ties break on the lower record id, so the result is deterministic. A record with a
    missing, non-numeric or non-finite (NaN, +/-infinity) modelled value is skipped rather than guessed; a candidate
    with one gets nothing. With no modelled columns there is nothing to compare, so the result is []. Returns []
    whenever nothing can be compared."""
    if not feature_columns:
        return []
    usable = []
    for rec in records:
        feats = rec.get("features") or {}
        if all(_is_finite_number(feats.get(c)) for c in feature_columns):
            usable.append(rec)
    if not usable or not all(_is_finite_number(candidate_features.get(c)) for c in feature_columns):
        return []
    spans = {}
    for c in feature_columns:
        values = [float(r["features"][c]) for r in usable]
        spans[c] = max(values) - min(values)
    scored = []
    for rec in usable:
        d2 = 0.0
        for c in feature_columns:
            if spans[c] > 0:
                d2 += ((float(candidate_features[c]) - float(rec["features"][c])) / spans[c]) ** 2
        scored.append((math.sqrt(d2), rec["id"], rec))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [
        {"reference": record_reference(rec["id"], rec.get("provenance")), "target": rec["target_value"],
         "distance": round(dist, 3)}
        for dist, _, rec in scored[:max(0, k)]
    ]


def candidate_gaps(domain_coverage, labels: dict) -> list:
    """Per-feature range findings for one candidate, from the stored range check only."""
    gaps = []
    if not domain_coverage:
        return gaps
    for col, f in domain_coverage["features"].items():
        if f["status"] != "within_historical_domain":
            gaps.append(f"{col} = {f['value']} is {labels[f['status']].lower()} "
                        f"(observed {f['historical_min']} to {f['historical_max']})")
    return gaps


def reference_condition_text(ingestion_review) -> str:
    """What is on record about the conditions the historical measurements were made under."""
    review = ingestion_review or {}
    cols = review.get("condition_columns") or []
    ref = (review.get("conditions") or {}).get("reference") or {}
    if cols and ref:
        return ("The historical records used were measured at these recorded test conditions: "
                + ", ".join(f"{k} = {v}" for k, v in ref.items())
                + ". A new physical result is comparable with them only under the same conditions.")
    if cols:
        return ("Test conditions were recorded for the historical data (" + ", ".join(cols) + "), but no single "
                "reference condition set is on record, so comparability of a new test with them is not established.")
    return ("No test conditions were declared for the historical data, so comparability of a new physical test with "
            "the historical records is not established by this system.")
