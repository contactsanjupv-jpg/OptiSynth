"""
Security-relevant action logging. `detail` must NEVER contain: passwords,
password hashes, API keys/tokens, session cookie values, or raw dataset
row contents -- only small, non-sensitive descriptors (e.g. a row count,
not the rows themselves). Every call site in this codebase follows that
rule; if you add a new one, keep following it.
"""
from backend.app.repositories import audit_repo


def log(organization_id, user_id, action: str, project_id=None, detail: str = None):
    audit_repo.write_audit_log(organization_id, user_id, action, project_id, detail)


def list_for_organization(organization_id: int, project_id: int = None):
    return audit_repo.list_audit_logs(organization_id, project_id)
