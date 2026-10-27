"""
Evidence-aware ranking: which candidates the historical data actually supports comparing, and in what order.

THE PROBLEM THIS SOLVES
  The numerical engine scores every candidate with the same Gaussian-process model and orders them by the
  model's estimated probability of meeting the requirement. A candidate whose inputs lie far outside
  everything the historical data covers still gets a score -- and the model extrapolates its trend, so that
  score can be an implausibly extreme value with probability 1.0. Because probability saturates at 1.0, many
  candidates tie, and the old tie-break (larger predicted value first) then rewarded the WILDEST
  extrapolation: a candidate at ~3x the observed inputs ranked first, above candidates the data supports. The
  per-candidate "Requires validation" label existed but never influenced the order.

THE RULE (one definition, used by the ranking run, the stored-prediction listing and the DOCX report so the
three can never disagree)
  Each candidate's evidence tier comes from decision_support, which is derived ONLY from the existing
  historical-range coverage check (change_case_rules.derive_decision_support):
    within_data_range   -> "ranked"                 every input inside the observed range (per feature)
    caution             -> "ranked_with_caution"    an input just beyond it (the product's heuristic edge
                                                    margin): ranked, but flagged provisional
    requires_validation -> "not_ranked_insufficient_evidence"
                           an input clearly outside the observed range, or coverage could not be determined
                           (fail-safe, same as decision support): NO POSITION -- neither first nor last
  Ranked candidates are ordered by the model's probability, exactly as before; ties are broken by evidence
  tier (inside the range before just beyond it), then by predicted value in the requirement's favorable
  direction, then by candidate id. The probability / predicted value / uncertainty themselves are NEVER
  modified, replaced or capped here.

WHY "NOT RANKED" AND NOT "RANKED LAST" OR A SUBSTITUTE PROBABILITY
  Being outside the observed data is not evidence that a candidate is bad -- it is the ABSENCE of evidence.
  Placing such a candidate last (or giving it 0% or 50%) would assert something the data cannot support just
  as surely as placing it first did. The honest outputs are: no position, the model's own numbers shown
  unchanged and labelled as extrapolation, and a statement that physical validation is what can decide.

WHAT THIS DOES NOT DO
  It does not make the model valid outside its data, does not validate the heuristic edge margin, and
  inherits the limits of the per-feature range check (it is not a joint applicability domain: a candidate
  inside every individual range can still sit in an empty region of the combined input space).
"""
from backend.app.services import change_case_rules

STATUS_RANKED = "ranked"
STATUS_RANKED_WITH_CAUTION = "ranked_with_caution"
STATUS_NOT_RANKED = "not_ranked_insufficient_evidence"

# evidence tier used as the tie-break after probability; lower sorts first. A candidate with no tier is not ranked.
_TIER_BY_DECISION = {
    change_case_rules.DECISION_WITHIN_DATA_RANGE: 0,
    change_case_rules.DECISION_CAUTION: 1,
}
_STATUS_BY_TIER = {0: STATUS_RANKED, 1: STATUS_RANKED_WITH_CAUTION}

ORDERING_RULE = (
    "Candidates whose inputs the historical data supports are ordered by model-estimated probability. "
    "Candidates with equal probability are ordered by evidence support (inside the observed range before just "
    "beyond it), then by predicted value in the requirement's favorable direction, then by candidate ID. "
    "Candidates clearly outside the observed range are not ranked."
)

STATEMENTS = {
    STATUS_RANKED: (
        "Ranked among candidates whose inputs lie within the range of the historical data, checked one "
        "feature at a time. The position reflects the model estimate, which is not a validated probability, "
        "and the range check does not show the model is accurate for this candidate; physical validation is "
        "still required before any candidate is considered qualified."
    ),
    STATUS_RANKED_WITH_CAUTION: (
        "Ranked provisionally: at least one input lies just beyond the observed range of the historical data "
        "(a mild extrapolation). Treat this position as provisional: physical validation is required "
        "before relying on it."
    ),
    STATUS_NOT_RANKED: (
        "Not ranked: at least one input lies outside the range the historical data covers, so the model's "
        "figures for this candidate are an extrapolation. Extrapolated estimates cannot be fairly compared "
        "with estimates the data supports, so no position is given -- neither first nor last. This does not "
        "mean the candidate is unsuitable: the evidence cannot say either way, and the model's stated "
        "uncertainty is not a reliable error bound outside the observed range. Physical validation (or "
        "historical data that covers these inputs) is required."
    ),
}
STATEMENT_COVERAGE_UNKNOWN = (
    "Not ranked: how well the historical data covers this candidate's inputs could not be determined, so the "
    "evidence cannot support a position. The model's figures are shown unchanged for information only. This "
    "does not mean the candidate is unsuitable. Physical validation is required."
)


def _tier(decision_support):
    """Evidence tier (0 or 1) or None when the candidate must not be ranked. Anything unrecognised -- no
    decision support at all, or an unknown status -- is None: it fails safe to not ranked."""
    if not isinstance(decision_support, dict):
        return None
    return _TIER_BY_DECISION.get(decision_support.get("status"))


def _statement(status, decision_support):
    if status == STATUS_NOT_RANKED:
        domain_status = decision_support.get("domain_status") if isinstance(decision_support, dict) else None
        return STATEMENT_COVERAGE_UNKNOWN if domain_status is None else STATEMENTS[STATUS_NOT_RANKED]
    return STATEMENTS[status]


def summarize_statuses(statuses: list) -> dict:
    """Counts and a plain-language message for a set of ranking statuses (None entries are ignored)."""
    statuses = [s for s in statuses if s]
    n = len(statuses)
    ranked = statuses.count(STATUS_RANKED)
    caution = statuses.count(STATUS_RANKED_WITH_CAUTION)
    not_ranked = statuses.count(STATUS_NOT_RANKED)
    if n == 0:
        message = "No candidates have been ranked."
    elif ranked + caution == 0:
        message = (
            "No candidate could be ranked: every candidate lies outside the range the historical data covers "
            "(or its coverage could not be determined), so no ordering is supported by evidence. The model's "
            "own estimates are shown for information only. Physical validation, or historical data covering "
            "these inputs, is needed before candidates can be compared. Not being ranked does not mean a "
            "candidate is unsuitable."
        )
    else:
        parts = []
        if not_ranked:
            parts.append(
                f"{not_ranked} of {n} candidates were not ranked because they lie outside the range the "
                "historical data covers; their model estimates are shown for information only and are not "
                "placed in the order. Not being ranked does not mean a candidate is unsuitable."
            )
        if caution:
            parts.append(
                f"{caution} of {n} candidates are ranked provisionally because an input lies just beyond the "
                "observed range."
            )
        message = " ".join(parts) if parts else f"All {n} candidates lie within the range of the historical data and are ranked."
    return {
        "candidates": n, "ranked": ranked, "ranked_with_caution": caution, "not_ranked": not_ranked,
        "message": message, "ordering_rule": ORDERING_RULE,
    }


def evidence_ranking(items: list, direction: str) -> dict:
    """items: [{"id", "predicted_probability", "predicted_value" (may be None), "decision_support"}].

    Returns {"by_id": {id: {"rank": int|None, "status": ..., "statement": ...}},
             "order": [ids, ranked first in rank order, then the unranked by id],
             "summary": summarize_statuses(...)}.
    Pure; never modifies its input or any number in it."""
    # Imported here, not at module level, so importing the statuses and wording (e.g. from the report builder)
    # does not load the numerical engine.
    from engine.facade import ranking_sort_key

    ranked_entries, unranked_entries = [], []
    for item in items:
        tier = _tier(item.get("decision_support"))
        (unranked_entries if tier is None else ranked_entries).append((item, tier))

    def sort_key(entry):
        item, tier = entry
        prob_key, value_key, tiebreak = ranking_sort_key(
            item["predicted_probability"], item.get("predicted_value"), item["id"], direction)
        return (prob_key, tier, value_key, tiebreak)

    ranked_entries.sort(key=sort_key)
    unranked_entries.sort(key=lambda entry: entry[0]["id"])  # by id: an order of ignorance, not of merit

    by_id = {}
    for position, (item, tier) in enumerate(ranked_entries, start=1):
        status = _STATUS_BY_TIER[tier]
        by_id[item["id"]] = {"rank": position, "status": status,
                             "statement": _statement(status, item.get("decision_support"))}
    for item, _ in unranked_entries:
        by_id[item["id"]] = {"rank": None, "status": STATUS_NOT_RANKED,
                             "statement": _statement(STATUS_NOT_RANKED, item.get("decision_support"))}
    return {
        "by_id": by_id,
        "order": [e[0]["id"] for e in ranked_entries] + [e[0]["id"] for e in unranked_entries],
        "summary": summarize_statuses([v["status"] for v in by_id.values()]),
    }
