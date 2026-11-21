import os
import re
from datetime import datetime, timedelta, timezone

from backend.app.repositories import admin_repo, organizations_repo, portfolio_repo
from backend.app.schemas.errors import Forbidden, ValidationError
from backend.app.services import organization_service, portfolio_service
from backend.app.services import qualification_dataset_service as uploads

ACTION_LABELS = {
    **portfolio_service.ACTIVITY_LABELS,
    "auth.signup": "Account created", "auth.login": "Signed in", "auth.logout": "Signed out",
    "auth.logout_all": "Signed out everywhere", "auth.password_change": "Password changed",
    "auth.profile_update": "Profile updated", "auth.session_revoke": "Session ended",
    "auth.sessions_revoke_others": "Other sessions ended",
    "organization.update": "Organization details updated", "data.export": "Data exported",
    "operator_activate_subscription": "Plan changed by operator",
}
_ID = re.compile(r"change_case_id=(\d+)")


def label_for(action: str) -> str:
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    return action.replace("_", " ").replace(".", " ").capitalize()


def _person(name, email):
    return name or email or None


def _day_start(value, field):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValidationError("Use the date format YYYY-MM-DD.", field=field)


def audit_history(organization_id: int, role: str, user_id=None, action=None, q=None, date_from=None, date_to=None,
                  limit=50, offset=0) -> dict:
    organization_service.require_admin(role)
    start, end = _day_start(date_from, "date_from"), _day_start(date_to, "date_to")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    total, rows = admin_repo.audit_events(organization_id, user_id, action, (q or "").strip() or None,
                                          start.isoformat() if start else None,
                                          (end + timedelta(days=1)).isoformat() if end else None, limit, offset)
    ids = sorted({int(m.group(1)) for r in rows if (m := _ID.search(r["detail"] or ""))})
    names = portfolio_repo.program_names(organization_id, ids)
    events = []
    for r in rows:
        m = _ID.search(r["detail"] or "")
        pid = int(m.group(1)) if m else None
        events.append({"id": r["id"], "action": r["action"], "label": label_for(r["action"]),
                       "actor": _person(r["actor_name"], r["actor_email"]), "actor_id": r["user_id"],
                       "program_id": pid if pid in names else None, "program_name": names.get(pid),
                       "detail": r["detail"], "created_at": r["created_at"]})
    members = admin_repo.export_members(organization_id)
    return {"total": total, "limit": limit, "offset": offset, "events": events,
            "actions": [{"action": a, "label": label_for(a)} for a in admin_repo.audit_actions(organization_id)],
            "actors": [{"id": m["id"], "name": _person(m["display_name"], m["email"])} for m in members]}


def security_events(organization_id: int, user_id: int) -> list:
    return [{"id": r["id"], "label": label_for(r["action"]), "detail": r["detail"], "created_at": r["created_at"]}
            for r in admin_repo.security_events(organization_id, user_id)]


def upload_inventory(organization_id: int, role: str) -> dict:
    organization_service.require_admin(role)
    files = []
    for r in admin_repo.upload_inventory(organization_id):
        path = os.path.join(uploads.UPLOAD_ROOT, str(organization_id), os.path.basename(r["stored_filename"] or ""))
        size = os.path.getsize(path) if os.path.isfile(path) else None
        files.append({"id": r["id"], "program_id": r["change_case_id"], "program_name": r["program_name"],
                      "filename": r["original_filename"], "rows": r["row_count"], "size_bytes": size,
                      "uploaded_by": _person(r["uploader_name"], r["uploader_email"]), "uploaded_at": r["created_at"]})
    return {"files": files, "total_bytes": sum(f["size_bytes"] or 0 for f in files)}


def export_all(organization_id: int, role: str) -> dict:
    """Everything stored for this organization that the product itself created, as plain JSON. Secrets are left out."""
    if role != "owner":
        raise Forbidden("Only an owner can export organization data.")
    org = organizations_repo.get_organization(organization_id)
    data = {"exported_at": datetime.now(timezone.utc).isoformat(),
            "organization": {k: org.get(k) for k in ("id", "name", "industry", "website", "created_at", "updated_at")},
            "members": admin_repo.export_members(organization_id)}
    for table in admin_repo.EXPORT_TABLES:
        data[table] = admin_repo.export_table(organization_id, table)
    return data
