"""
Pure rules for managing a candidate substitute after it exists: when it can be edited, removed from
the analysis (archived), restored or permanently deleted, and when a stored prediction still
describes it. No database / web-framework imports (same pattern as change_case_rules.py and
change_case_lifecycle_rules.py), so every rule is directly unit-testable.

THE MODEL
  * A candidate belongs to ONE diagnostic and is identified by its id, never by its name.
  * Editing the candidate's feature values (or the units they were entered in) bumps
    `inputs_version`. Every prediction stores the `inputs_version` it was computed for. A prediction
    is "for the current inputs" only while the two are equal. After an edit the old prediction is
    KEPT as history but is never shown (screen or report) as if it described the new values, and the
    candidate's rank, domain coverage and decision support are withheld until the ranking is re-run.
  * Adding, removing, restoring or editing a candidate means the ranking no longer covers the candidates as
    they are now, so the order shown for the others is no longer the order a run over them would give:
    ranks are withheld for ALL candidates until the ranking is re-run. (Each candidate's own prediction
    is kept and still labelled with the dataset and, after an edit, the input values it was computed for.)
  * Permanent deletion is only for a candidate nothing depends on (never ranked, no recorded
    physical outcome). Anything else is "removed from the analysis" (archived): retained, restorable,
    excluded from ranking. Recorded physical outcomes are never deleted by any path here.
  * Once a physical outcome is recorded for a candidate, its feature values are locked: the outcome
    describes the material that was tested, so the inputs it is compared with must not change.
    (Renaming is still allowed -- it changes a label, not the evidence.)
"""
from datetime import datetime, timezone

from backend.app.schemas.errors import ValidationError

NAME_MAX_LENGTH = 200
EDITABLE_FIELDS = {"name", "features", "units"}

INPUTS_CHANGED_NOTICE = (
    "This candidate's input values were changed after the ranking ran, so the prediction shown is for its "
    "previous inputs. Re-run ranking to get a prediction for the current values."
)
ORDER_OUTDATED_NOTICE = (
    "Candidates were added, removed, restored or edited after this ranking ran, so the order is out of "
    "date. Re-run ranking to rank the candidates as they are now."
)


def is_archived(candidate: dict) -> bool:
    return bool(candidate.get("archived_at"))


def check_case_open(case: dict) -> None:
    """A completed diagnostic is a closed record: its candidates can't be changed. (An archived one is
    refused earlier, by the route, with its own message.)"""
    if case.get("status") not in ("draft", "active"):
        raise ValidationError(
            f"Can't change candidates of a diagnostic with status '{case.get('status')}': "
            "a completed diagnostic is a closed record.",
            field="status",
        )


def check_candidate_not_archived(candidate: dict) -> None:
    if is_archived(candidate):
        raise ValidationError(
            "This candidate has been removed from the analysis. Restore it first if you need to change it.",
            field="archived",
        )


def clean_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("A candidate needs a name.", field="name")
    value = value.strip()
    if len(value) > NAME_MAX_LENGTH:
        raise ValidationError(f"The name is too long (max {NAME_MAX_LENGTH} characters).", field="name")
    return value


def check_features_known(feature_columns, features: dict, candidate_name: str = None) -> None:
    """Every key must be one of the diagnostic's modelled input features. A value for anything else
    would be stored and shown beside the candidate although the model never reads it, so it is
    refused instead of silently kept (names are case-sensitive: they must match the spec exactly)."""
    if not feature_columns:
        return
    unknown = sorted(k for k in features if k not in set(feature_columns))
    if unknown:
        who = f"Candidate '{candidate_name}'" if candidate_name else "This candidate"
        raise ValidationError(
            f"{who} has value(s) for field(s) this diagnostic does not model: {', '.join(unknown)}. "
            f"Only these input features are used: {', '.join(feature_columns)}.",
            field="features",
        )


def usage_of(usage: dict) -> dict:
    return {
        "prediction_count": int((usage or {}).get("prediction_count", 0) or 0),
        "outcome_count": int((usage or {}).get("outcome_count", 0) or 0),
    }


def delete_blocked_reason(candidate: dict, usage: dict):
    """None if the candidate may be permanently deleted, else a plain-language reason (and the
    alternative). Deletion is for a candidate added in error that nothing depends on."""
    u = usage_of(usage)
    if u["outcome_count"] > 0:
        return ("A physical validation outcome has been recorded for it. That evidence is retained; "
                "remove it from the analysis instead.")
    if u["prediction_count"] > 0:
        return ("It has been ranked, so results exist that a decision may have rested on. They are retained "
                "as history; remove it from the analysis instead.")
    return None


def features_locked_reason(candidate: dict, usage: dict):
    """None if the candidate's feature values may still be changed, else why not."""
    if is_archived(candidate):
        return "The candidate has been removed from the analysis."
    if usage_of(usage)["outcome_count"] > 0:
        return ("A physical validation outcome has been recorded for this candidate, so its input values can "
                "no longer be changed -- the outcome describes the material that was tested. Add a new "
                "candidate if the values were wrong.")
    return None


def lifecycle_flags(candidate: dict, usage: dict) -> dict:
    """What depends on the candidate and what may be done with it. Derived on every read -- never
    stored, so it cannot drift from the real data."""
    u = usage_of(usage)
    blocked = delete_blocked_reason(candidate, usage)
    locked = features_locked_reason(candidate, usage)
    return {
        "archived": is_archived(candidate),
        "prediction_count": u["prediction_count"],
        "outcome_count": u["outcome_count"],
        "deletable": blocked is None,
        "delete_blocked_reason": blocked,
        "features_editable": locked is None,
        "features_locked_reason": locked,
    }


def check_can_edit(case: dict, candidate: dict, changes: dict) -> None:
    """`changes`: only the keys the caller sent."""
    if not changes:
        raise ValidationError("Nothing to change.", field="changes")
    unknown = sorted(set(changes) - EDITABLE_FIELDS)
    if unknown:
        raise ValidationError("These fields can't be edited: " + ", ".join(unknown) + ".", field="changes")
    if "units" in changes and "features" not in changes:
        raise ValidationError(
            "Units describe the values being entered, so send the feature values together with their units.",
            field="units",
        )
    check_case_open(case)
    check_candidate_not_archived(candidate)


def check_features_editable(candidate: dict, usage: dict) -> None:
    reason = features_locked_reason(candidate, usage)
    if reason:
        raise ValidationError(f"The input values can't be changed. {reason}", field="features")


def check_can_delete(candidate: dict, usage: dict) -> None:
    reason = delete_blocked_reason(candidate, usage)
    if reason:
        raise ValidationError(f"This candidate can't be permanently deleted. {reason}", field="delete")


def check_can_archive(candidate: dict) -> None:
    if is_archived(candidate):
        raise ValidationError("This candidate has already been removed from the analysis.", field="archived")


def check_can_restore(candidate: dict) -> None:
    if not is_archived(candidate):
        raise ValidationError("This candidate has not been removed from the analysis.", field="archived")


def prediction_matches_inputs(candidate: dict, prediction) -> bool | None:
    """True if the stored prediction was computed for the candidate's CURRENT inputs, False if the
    inputs were edited since, None if there is no prediction."""
    if prediction is None:
        return None
    return int(prediction.get("inputs_version") or 0) == int(candidate.get("inputs_version") or 0)


def _when(stamp: str) -> datetime:
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def ranking_set_changed(active: list, archived: list) -> bool:
    """True if candidates were added, removed or restored AFTER the latest ranking run, so the order
    shown for the others no longer comes from a run over the current set.

    Uses only facts that are always stored (no run identifier is needed, so old rankings are judged the
    same way as new ones):
      active:   [{"latest_prediction_created_at": str|None, "restored_at": str|None}] -- the candidates
                currently in the analysis;
      archived: [{"archived_at": str|None, "prediction_count": int}] -- candidates removed from it.
    R = the time of the latest ranking among the active candidates. The set changed if
      (a) an active candidate has never been ranked while others have (added after the run), or
      (b) an active candidate was restored after R, or
      (c) a candidate that had been ranked was removed after R.
    """
    ranked_at = [c["latest_prediction_created_at"] for c in active if c.get("latest_prediction_created_at")]
    if not ranked_at:
        return False
    latest_run = max(_when(t) for t in ranked_at)
    if any(not c.get("latest_prediction_created_at") for c in active):
        return True
    if any(c.get("restored_at") and _when(c["restored_at"]) > latest_run for c in active):
        return True
    return any(
        a.get("prediction_count", 0) > 0 and a.get("archived_at") and _when(a["archived_at"]) > latest_run
        for a in archived
    )