import re
from datetime import datetime, timedelta, timezone

from backend.app.repositories import portfolio_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import portfolio_rules as rules
from backend.app.services import program_stage_rules as stages

RANGE_DAYS = (7, 30, 90, 365, 0)  # 0 means all time
DEFAULT_DAYS = 90
FILTERS = ("active", "evidence_review", "tests_pending", "attention")
_ID = re.compile(r"change_case_id=(\d+)")

ACTIVITY_LABELS = {
    "change_case.create": "Program created",
    "change_case.update": "Program details updated",
    "change_case.status_update": "Status changed",
    "change_case.stage_change": "Stage changed",
    "change_case.archive": "Program archived",
    "change_case.restore": "Program restored",
    "change_case.rank": "Candidates ranked",
    "change_case.report_generate": "Report generated",
    "qualification_dataset.upload": "Evidence uploaded",
    "candidate.import": "Candidates imported",
    "candidate.add": "Candidate added",
    "candidate.update": "Candidate updated",
    "candidate.archive": "Candidate archived",
    "candidate.restore": "Candidate restored",
    "qualification_outcome.record": "Physical result recorded",
}


def _days(value) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    if days not in RANGE_DAYS:
        raise ValidationError("Choose 7, 30, 90 or 365 days, or 0 for all time.", field="days")
    return days


def _since(days: int):
    return None if days == 0 else (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _person(name, email):
    return name or email or None


def _programs(organization_id: int) -> list:
    out = []
    for f in portfolio_repo.program_facts(organization_id):
        issues = rules.derive_issues(f)
        f["issues"] = issues
        f["severity"] = rules.top_severity(issues)
        f["needs_attention"] = rules.needs_attention(issues)
        f["created_by"] = _person(f.pop("creator_name"), f.pop("creator_email"))
        f["stage_label"] = stages.label(f["stage"])
        out.append(f)
    return out


def _summary_row(p: dict) -> dict:
    return {"id": p["id"], "name": p["name"], "status": p["status"], "stage": p["stage"], "stage_label": p["stage_label"],
            "severity": p["severity"], "issues": p["issues"], "created_by": p["created_by"], "updated_at": p["updated_at"]}


def _in_progress(p: dict) -> bool:
    return p["status"] == "active" and p["stage"] not in (stages.APPROVED, stages.REJECTED, stages.CLOSED)


def overview(organization_id: int, days_value=DEFAULT_DAYS) -> dict:
    days = _days(days_value)
    since = _since(days)
    programs = _programs(organization_id)
    active = [p for p in programs if p["status"] == "active"]

    pipeline = [{"stage": s, "label": stages.label(s), "count": sum(1 for p in programs if p["stage"] == s)}
                for s in stages.ALL_STAGES]

    # One action per open issue, most urgent first, each opening its program.
    actions = []
    for p in programs:
        for issue in p["issues"]:
            actions.append({"type": issue["type"], "severity": issue["severity"], "label": issue["label"],
                            "detail": issue["detail"], "program_id": p["id"], "program_name": p["name"],
                            "updated_at": p["updated_at"]})
    actions.sort(key=lambda a: (rules.SEVERITY_ORDER[a["severity"]], _neg(a["updated_at"])))

    by_type = {}
    for a in actions:
        if a["severity"] in (rules.HIGH, rules.MEDIUM):
            by_type[a["type"]] = by_type.get(a["type"], 0) + 1
    issue_types = [{"type": k, "label": rules.ISSUE_LABELS[k], "count": v} for k, v in
                   sorted(by_type.items(), key=lambda kv: -kv[1])]

    attention = [_summary_row(p) for p in programs if p["needs_attention"]]
    attention.sort(key=lambda r: (rules.SEVERITY_ORDER[r["severity"]], _neg(r["updated_at"])))

    names = {p["id"]: p["name"] for p in programs}
    audit = portfolio_repo.recent_audit(organization_id, since)
    missing = sorted({int(m.group(1)) for a in audit if (m := _ID.search(a["detail"] or "")) and int(m.group(1)) not in names})
    names.update(portfolio_repo.program_names(organization_id, missing))
    activity = []
    for a in audit:
        label = ACTIVITY_LABELS.get(a["action"])
        if not label:
            continue
        m = _ID.search(a["detail"] or "")
        pid = int(m.group(1)) if m else None
        activity.append({"id": a["id"], "label": label, "program_id": pid if pid in names else None,
                         "program_name": names.get(pid), "actor": _person(a["actor_name"], a["actor_email"]),
                         "created_at": a["created_at"]})
        if len(activity) == 12:
            break

    decisions = [{"id": d["id"], "program_id": d["change_case_id"], "program_name": d["program_name"],
                  "outcome": stages.label(d["to_stage"]), "reason": d["reason"],
                  "actor": _person(d["actor_name"], d["actor_email"]), "created_at": d["created_at"]}
                 for d in portfolio_repo.recent_decisions(organization_id, since)]

    return {
        "range_days": days,
        "total_programs": len(programs),
        "kpis": {
            "active_programs": len(active),
            "evidence_review": sum(1 for p in programs if p["stage"] == stages.EVIDENCE_REVIEW and p["status"] != "completed"),
            "tests_pending": sum(p["pending_test_count"] for p in active),
            "needs_attention": len(attention),
        },
        "pipeline": pipeline,
        "status_summary": {
            "no_open_issues": sum(1 for p in active if _in_progress(p) and not p["issues"]),
            "open_issues": sum(1 for p in active if p["issues"]),
            "high_priority": sum(1 for p in active if p["severity"] == rules.HIGH),
        },
        "attention": attention[:8],
        "actions": actions[:12],
        "issue_types": issue_types,
        "decisions": decisions,
        "activity": activity,
    }


def _neg(iso):
    """Sort key that puts the newest timestamp first inside an ascending sort."""
    return tuple(-ord(c) for c in (iso or ""))


def programs(organization_id: int, filter_name=None, stage=None, issue=None, q=None) -> dict:
    if filter_name and filter_name not in FILTERS:
        raise ValidationError("Unknown filter.", field="filter")
    wanted = [x for x in (stage or "").split(",") if x]
    if any(x not in stages.ALL_STAGES for x in wanted):
        raise ValidationError("Unknown stage.", field="stage")
    rows = _programs(organization_id)
    if filter_name == "active":
        rows = [p for p in rows if p["status"] == "active"]
    elif filter_name == "evidence_review":
        rows = [p for p in rows if p["stage"] == stages.EVIDENCE_REVIEW and p["status"] != "completed"]
    elif filter_name == "tests_pending":
        rows = [p for p in rows if p["status"] == "active" and p["pending_test_count"] > 0]
    elif filter_name == "attention":
        rows = [p for p in rows if p["needs_attention"]]
    if wanted:
        rows = [p for p in rows if p["stage"] in wanted]
    if issue:
        rows = [p for p in rows if any(i["type"] == issue for i in p["issues"])]
    if q:
        needle = q.strip().lower()
        rows = [p for p in rows if needle in p["name"].lower()]
    return {"programs": [_summary_row(p) for p in rows], "total": len(rows)}
