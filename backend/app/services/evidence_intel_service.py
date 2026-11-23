import json
import logging
from datetime import datetime, timezone

from backend.app.repositories import evidence_intel_repo as repo, notifications_repo, program_workspace_repo
from backend.app.schemas.errors import Forbidden, ValidationError
from backend.app.services import evidence_intel_rules as rules
from backend.app.services import program_workspace_service as workspace

logger = logging.getLogger("rdopt")
REVIEWER_MESSAGE = "Only an owner, an admin, the program owner or a program reviewer can do this."
CONFLICT_RESOLUTIONS = {"accepted_as_is": "resolved", "prefer_dataset": "resolved", "not_a_conflict": "dismissed"}


def _person(name, email):
    return name or email or None


def _can_review(organization_id: int, role: str, user_id: int, case: dict) -> bool:
    return (role in ("owner", "admin") or case.get("owner_user_id") == user_id
            or program_workspace_repo.is_reviewer(organization_id, case["id"], user_id))


def _require_reviewer(organization_id: int, role: str, user_id: int, case: dict) -> None:
    if not _can_review(organization_id, role, user_id, case):
        raise Forbidden(REVIEWER_MESSAGE)


def _json(value, default):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _documents(organization_id: int, case_id: int) -> list:
    """Every document of the program with its parsed review record and rows."""
    out = []
    datasets = repo.list_for_case(organization_id, case_id)
    newest_accepted = max((d["id"] for d in datasets if d["review_status"] == rules.ACCEPTED), default=None)
    for n, d in enumerate(datasets, start=1):
        review = _json(d.get("review_json"), {})
        rows = []
        for r in repo.rows_for_dataset(organization_id, d["id"]):
            prov = _json(r.get("provenance_json"), {})
            rows.append({"id": r["id"], "features": _json(r["features_json"], {}), "target": r["target_value"],
                         "source_row": prov.get("row"), "provenance": prov})
        out.append({**d, "version_number": n, "filename": d["original_filename"], "status": d["review_status"], "review": review,
                    "rows": rows, "sha": review.get("file_sha256"), "is_current": d["id"] == newest_accepted})
    return out


def _summary(d: dict, open_conflicts: dict) -> dict:
    return {"id": d["id"], "version_number": d["version_number"], "filename": d["filename"], "uploaded_at": d["created_at"],
            "uploaded_by": _person(d["uploader_name"], d["uploader_email"]), "row_count": d["row_count"],
            "program": {"id": d["change_case_id"], "name": d["program_name"]},
            "status": d["status"], "status_label": rules.STATUS_LABELS[d["status"]], "review_reason": d["review_reason"],
            "reviewed_by": _person(d["reviewer_name"], d["reviewer_email"]), "reviewed_at": d["reviewed_at"],
            "document_type": d["document_type"], "document_type_label": rules.DOCUMENT_TYPES.get(d["document_type"]),
            "source_note": d["source_note"], "document_version": d["document_version"], "test_method": d["test_method"],
            "document_date": d["document_date"], "source_reliability": d["source_reliability"],
            "superseded_by_dataset_id": d["superseded_by_dataset_id"], "file_sha256": d["sha"], "is_current": d["is_current"],
            "open_conflicts": open_conflicts.get(d["id"], 0)}


def scan(organization_id: int, case_id: int) -> dict:
    docs = _documents(organization_id, case_id)
    detected = rules.detect_conflicts(docs)
    return repo.sync_conflicts(organization_id, case_id, detected)


def scan_quietly(organization_id: int, case_id: int) -> None:
    """Used after a change. A failed scan must not undo or hide the change itself, so it is logged."""
    try:
        scan(organization_id, case_id)
    except Exception:
        logger.exception("evidence conflict scan failed for change case %s", case_id)


def _conflict_view(c: dict, versions: dict) -> dict:
    return {"id": c["id"], "kind": c["kind"], "severity": c["severity"], "title": c["title"], "description": c["description"],
            "dataset_ids": c["dataset_ids"], "documents": [versions.get(i) for i in c["dataset_ids"]], "details": c["details"],
            "status": c["status"], "resolution": c["resolution"], "resolution_note": c["resolution_note"],
            "preferred_dataset_id": c["preferred_dataset_id"], "detected_at": c["detected_at"],
            "resolved_at": c["resolved_at"], "resolved_by": _person(c["resolver_name"], c["resolver_email"])}


def documents(organization_id: int, role: str, user_id: int, case_id: int) -> dict:
    case = workspace._case(organization_id, case_id)
    docs = _documents(organization_id, case_id)
    counts = repo.open_conflict_dataset_counts(organization_id)
    versions = {d["id"]: f"{d['filename']} (version {d['version_number']})" for d in docs}
    latest = next((d for d in reversed(docs) if d["status"] == rules.ACCEPTED), None)
    held = repo.newest_held_after(organization_id, case_id, latest["id"] if latest else 0)
    writable = case["status"] != "completed" and not case.get("archived_at")
    return {
        "documents": [_summary(d, counts) for d in reversed(docs)],
        "conflicts": [_conflict_view(c, versions) for c in repo.list_conflicts(organization_id, case_id)],
        "held_notice": None if not held else f"{held['original_filename']} is under review ({rules.STATUS_LABELS[held['review_status']].lower()}). "
                       "Ranking is paused until it is accepted, rejected, excluded or superseded.",
        "permissions": {"can_review": writable and _can_review(organization_id, role, user_id, case), "writable": writable},
        "types": [{"key": k, "label": v} for k, v in rules.DOCUMENT_TYPES.items()],
        "reliability": [{"key": k, "label": v} for k, v in rules.RELIABILITY.items()],
    }


def _doc(organization_id: int, case_id: int, dataset_id: int):
    docs = _documents(organization_id, case_id)
    doc = next((d for d in docs if d["id"] == dataset_id), None)
    if doc is None:
        raise LookupError("Evidence document not found.")
    return doc, docs


def detail(organization_id: int, role: str, user_id: int, case_id: int, dataset_id: int) -> dict:
    case = workspace._case(organization_id, case_id)
    doc, docs = _doc(organization_id, case_id, dataset_id)
    counts = repo.open_conflict_dataset_counts(organization_id)
    history = [{"id": h["id"], "action": h["action"], "label": rules.ACTION_LABELS.get(h["action"], h["action"]),
                "from_status": h["from_status"], "to_status": h["to_status"], "reason": h["reason"],
                "related_dataset_id": h["related_dataset_id"], "actor": _person(h["actor_name"], h["actor_email"]),
                "created_at": h["created_at"]} for h in repo.review_history(organization_id, dataset_id)]
    today = datetime.now(timezone.utc).date().isoformat()
    return {**_summary(doc, counts), "quality": rules.quality_dimensions(doc, today), "history": history,
            "others": [{"id": d["id"], "label": f"{d['filename']} (version {d['version_number']})"} for d in docs
                       if d["id"] != dataset_id and d["status"] == rules.ACCEPTED],
            "flags": [f for f in doc["review"].get("flags", []) if f.get("severity") != "info"][:50],
            "writable": case["status"] != "completed" and not case.get("archived_at"),
            "can_review": _can_review(organization_id, role, user_id, case)}


def update_metadata(organization_id: int, role: str, user_id: int, case_id: int, dataset_id: int, changes: dict) -> list:
    case = workspace._writable(organization_id, case_id)
    doc, _ = _doc(organization_id, case_id, dataset_id)
    if not (_can_review(organization_id, role, user_id, case) or doc["uploaded_by_user_id"] == user_id):
        raise Forbidden("Only the person who uploaded this document, or someone who can review evidence, can change its details.")
    fields = rules.clean_metadata(changes)
    repo.update_metadata(organization_id, dataset_id, fields)
    scan_quietly(organization_id, case_id)
    return sorted(fields)


def review(organization_id: int, role: str, user_id: int, case_id: int, dataset_id: int, action: str, reason, superseded_by) -> dict:
    case = workspace._writable(organization_id, case_id)
    _require_reviewer(organization_id, role, user_id, case)
    doc, docs = _doc(organization_id, case_id, dataset_id)
    to_status, cleaned = rules.check_review(action, doc["status"], reason, superseded_by, dataset_id)
    if action == "supersede":
        newer = next((d for d in docs if d["id"] == superseded_by), None)
        if newer is None:
            raise ValidationError("The replacing document must belong to this program.", field="superseded_by")
        if newer["status"] != rules.ACCEPTED:
            raise ValidationError("The replacing document must be accepted evidence.", field="superseded_by")
        if newer["id"] < doc["id"]:
            raise ValidationError("The replacing document must be newer than the one it replaces.", field="superseded_by")
    if not repo.set_review(organization_id, case_id, dataset_id, action, doc["status"], to_status, cleaned,
                           superseded_by if action == "supersede" else None, user_id):
        raise ValidationError("The review status was changed by someone else. Reload and try again.", field="action")
    if action in ("reject", "request_clarification", "flag_conflict", "exclude") and doc["uploaded_by_user_id"] not in (None, user_id):
        notifications_repo.create_many(organization_id, [doc["uploaded_by_user_id"]], "evidence_review",
                                       f"Evidence {rules.ACTION_LABELS[action].lower()}", doc["filename"], f"/programs/{case_id}?tab=evidence")
    scan_quietly(organization_id, case_id)
    return detail(organization_id, role, user_id, case_id, dataset_id)


def rows(organization_id: int, case_id: int, dataset_id: int, offset: int = 0, limit: int = 50) -> dict:
    workspace._case(organization_id, case_id)
    doc, _ = _doc(organization_id, case_id, dataset_id)
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    page = doc["rows"][offset:offset + limit]
    flags = doc["review"].get("flags", [])
    out = []
    for i, r in enumerate(page, start=offset + 1):
        prov = r["provenance"] or {}
        values = prov.get("values")
        src = prov.get("row")
        out.append({"id": r["id"], "number": i, "inputs": r["features"], "target": r["target"],
                    "source": {"file": prov.get("file"), "table": prov.get("table"), "location": prov.get("location"), "row": src} if prov else None,
                    "values": values,
                    "trace_note": None if values else "The original values were not recorded for this upload. Only the stored values are shown.",
                    "source_flags": [{"code": f.get("code"), "severity": f.get("severity"), "message": f.get("message")}
                                     for f in flags if src is not None and src in (f.get("rows") or [])]})
    return {"dataset": {"id": doc["id"], "version_number": doc["version_number"], "filename": doc["filename"],
                        "status": doc["status"], "status_label": rules.STATUS_LABELS[doc["status"]],
                        "reviewed_by": _person(doc["reviewer_name"], doc["reviewer_email"]), "reviewed_at": doc["reviewed_at"],
                        "file_sha256": doc["sha"], "uploaded_by": _person(doc["uploader_name"], doc["uploader_email"])},
            "total": len(doc["rows"]), "offset": offset, "limit": limit, "rows": out}


def resolve_conflict(organization_id: int, role: str, user_id: int, case_id: int, conflict_id: int, resolution: str, note, preferred) -> dict:
    from backend.app.services import program_workspace_rules as w
    case = workspace._writable(organization_id, case_id)
    _require_reviewer(organization_id, role, user_id, case)
    conflict = repo.get_conflict(organization_id, case_id, conflict_id)
    if conflict is None:
        raise LookupError("Conflict not found.")
    if conflict["status"] != "open":
        raise ValidationError("This conflict has already been resolved.", field="resolution")
    if resolution not in CONFLICT_RESOLUTIONS:
        raise ValidationError("Choose how this conflict is resolved.", field="resolution")
    cleaned = w.clean_text(note, "note", 2000, "The note", required=True)
    if len(cleaned) < 3:
        raise ValidationError("Explain the decision in a few words.", field="note")
    if resolution == "prefer_dataset":
        if preferred not in conflict["dataset_ids"]:
            raise ValidationError("Choose one of the documents involved in this conflict.", field="preferred_dataset_id")
    else:
        preferred = None
    if not repo.resolve_conflict(organization_id, conflict_id, CONFLICT_RESOLUTIONS[resolution], resolution, cleaned, preferred, user_id):
        raise ValidationError("This conflict has already been resolved.", field="resolution")
    return documents(organization_id, role, user_id, case_id)


def library(organization_id: int, q=None, document_type=None, status=None, program_id=None, limit: int = 50, offset: int = 0) -> dict:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    if document_type and document_type not in rules.DOCUMENT_TYPES:
        raise ValidationError("Unknown document type.", field="document_type")
    if status and status not in rules.STATUS_LABELS:
        raise ValidationError("Unknown review status.", field="status")
    total, rows_ = repo.library(organization_id, (q or "").strip() or None, document_type, status, program_id, limit, offset)
    counts = repo.open_conflict_dataset_counts(organization_id)
    versions = {}
    items = []
    for d in rows_:
        if d["change_case_id"] not in versions:
            versions[d["change_case_id"]] = {x["id"]: n for n, x in enumerate(repo.list_for_case(organization_id, d["change_case_id"]), start=1)}
        d = {**d, "version_number": versions[d["change_case_id"]].get(d["id"]), "filename": d["original_filename"], "status": d["review_status"],
             "sha": _json(d.get("review_json"), {}).get("file_sha256"), "is_current": False}
        items.append(_summary(d, counts))
    return {"total": total, "limit": limit, "offset": offset, "items": items,
            "types": [{"key": k, "label": v} for k, v in rules.DOCUMENT_TYPES.items()],
            "statuses": [{"key": k, "label": v} for k, v in rules.STATUS_LABELS.items()]}
