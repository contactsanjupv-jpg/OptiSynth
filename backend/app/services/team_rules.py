"""
Pure business rules for team management, with NO fastapi/sqlalchemy/pydantic
import -- same pattern as services/project_rules.py, and for the same
reason: this logic is testable in this sandbox even though the routes that
call it aren't.
"""
from datetime import datetime, timezone

from backend.app.schemas.errors import ValidationError

MANAGE_ROLES = {"owner", "admin"}


def check_can_manage_team(actor_role: str) -> None:
    """Only owners and admins can invite, remove, or change roles.
    Raises PermissionError (mapped to HTTP 403 by middleware/error_handling.py)
    -- this is an authorization failure, not a validation failure."""
    if actor_role not in MANAGE_ROLES:
        raise PermissionError("Only owners and admins can manage team members.")


def check_not_removing_last_owner(target_role: str, owner_count: int) -> None:
    """Raises ValidationError if removing/demoting this member would leave
    the organization with zero owners."""
    if target_role == "owner" and owner_count <= 1:
        raise ValidationError(
            "Can't remove the last owner of an organization. Promote another "
            "member to owner first."
        )


def check_invitation_usable(invitation: dict, now: datetime = None) -> None:
    """Raises ValidationError if an invitation can't be accepted right now
    -- already used, revoked, or past its expiry."""
    now = now or datetime.now(timezone.utc)
    if invitation["status"] != "pending":
        raise ValidationError("This invitation is no longer valid.")
    expires_at = invitation["expires_at"]
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if now > expires_at:
        raise ValidationError("This invitation is no longer valid.")