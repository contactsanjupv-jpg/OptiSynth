"""Which open issues a program has, derived only from its stored records. Pure: no database access.

Nothing here is a model judgement. Each issue names a missing or out of date record that someone can act on."""
from backend.app.services import program_stage_rules as stages

HIGH, MEDIUM, LOW = "high", "medium", "low"
SEVERITY_ORDER = {HIGH: 0, MEDIUM: 1, LOW: 2}

# type -> (severity, label)
ISSUES = {
    "decision_needed": (HIGH, "Engineering decision needed"),
    "failed_result": (HIGH, "A physical result did not meet the target"),
    "ranking_stale": (MEDIUM, "Ranking is older than the latest evidence"),
    "inputs_changed": (MEDIUM, "Candidate inputs changed after ranking"),
    "test_pending": (MEDIUM, "Physical results pending"),
    "ranking_needed": (MEDIUM, "Candidates need ranking"),
    "evidence_needed": (LOW, "No evidence uploaded"),
    "candidates_needed": (LOW, "No candidates added"),
}
ISSUE_LABELS = {k: v[1] for k, v in ISSUES.items()}

_AFTER_RANKING = (stages.EXPERIMENT_PLANNING, stages.PHYSICAL_VALIDATION, stages.ENGINEERING_REVIEW)


def derive_issues(f: dict) -> list:
    """`f` holds the program's stage, status and record counts. Returns [{type, severity, label, detail}] for a program
    that is in progress. Drafts, closed and decided programs have no open issues."""
    if f["status"] != "active" or f["stage"] in (stages.APPROVED, stages.REJECTED, stages.CLOSED):
        return []
    found = []

    def add(kind, detail=None):
        severity, label = ISSUES[kind]
        found.append({"type": kind, "severity": severity, "label": label, "detail": detail})

    if f["stage"] == stages.ENGINEERING_REVIEW:
        add("decision_needed")
    if f["failed_outcome_count"] > 0:
        n = f["failed_outcome_count"]
        add("failed_result", f"{n} result{'s' if n != 1 else ''}")
    if f["stale_ranked_count"] > 0:
        n = f["stale_ranked_count"]
        add("ranking_stale", f"{n} candidate{'s' if n != 1 else ''}")
    if f["inputs_changed_count"] > 0:
        n = f["inputs_changed_count"]
        add("inputs_changed", f"{n} candidate{'s' if n != 1 else ''}")
    if f["pending_test_count"] > 0 and f["stage"] in (stages.EXPERIMENT_PLANNING, stages.PHYSICAL_VALIDATION):
        n = f["pending_test_count"]
        add("test_pending", f"{n} candidate{'s' if n != 1 else ''}")
    if f["unranked_count"] > 0 and f["dataset_count"] > 0 and f["stage"] in (stages.CANDIDATE_ASSESSMENT,) + _AFTER_RANKING:
        n = f["unranked_count"]
        add("ranking_needed", f"{n} candidate{'s' if n != 1 else ''}")
    if f["dataset_count"] == 0 and f["stage"] in (stages.EVIDENCE_COLLECTION, stages.EVIDENCE_REVIEW):
        add("evidence_needed")
    if f["candidate_count"] == 0 and f["stage"] == stages.CANDIDATE_ASSESSMENT:
        add("candidates_needed")
    found.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    return found


def needs_attention(issues: list) -> bool:
    return any(i["severity"] in (HIGH, MEDIUM) for i in issues)


def top_severity(issues: list):
    return issues[0]["severity"] if issues else None
