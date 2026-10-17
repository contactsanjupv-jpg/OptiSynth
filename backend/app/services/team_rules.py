"""
Pure business rules for team management, with NO fastapi/sqlalchemy/pydantic
import -- same pattern as services/project_rules.py, and for the same
reason: this logic is testable in this sandbox even though the routes that
call it aren't.
"""
from datetime import datetime, timezone

from backend.app.schemas.errors import ValidationError

MANAGE_ROLES = {"owner", "admin"}
VALID_ROLES = {"owner", "admin", "member"}


def check_can_manage_team(actor_role: str) -> None:
    """Only owners and admins can invite, remove, or change roles.
    Raises PermissionError (mapped to HTTP 403 by middleware/error_handling.py)
    -- this is an authorization failure, not a validation failure."""
    if actor_role not in MANAGE_ROLES:
        raise PermissionError("Only owners and admins can manage team members.")


def check_can_invite_with_role(actor_role: str, invited_role: str) -> None:
    """Owner status can only be handed out by an owner. An admin may invite admins and members,
    but never an owner -- otherwise an admin could mint a second account with full owner rights."""
    check_can_manage_team(actor_role)
    if invited_role not in VALID_ROLES:
        raise ValidationError(f"Unknown role: {invited_role!r}.", field="role")
    if invited_role == "owner" and actor_role != "owner":
        raise PermissionError("Only an owner can invite someone as an owner.")


def check_can_change_role(actor_role: str, actor_user_id: int, target_user_id: int,
                          target_current_role: str, new_role: str) -> None:
    """Role changes, enforced on the server (hiding a UI option is not a control).
      * only owners and admins manage roles at all;
      * nobody changes their OWN role (an admin cannot promote themselves, and an owner cannot
        demote themselves by accident -- another owner can do that);
      * only an owner can grant owner, and only an owner can change an existing owner's role.
    The last-owner rule is enforced separately and atomically by the repository."""
    check_can_manage_team(actor_role)
    if new_role not in VALID_ROLES:
        raise ValidationError(f"Unknown role: {new_role!r}.", field="role")
    if actor_user_id == target_user_id:
        raise ValidationError("You can't change your own role. Ask another owner to do it.", field="role")
    if actor_role != "owner" and (new_role == "owner" or target_current_role == "owner"):
        raise PermissionError("Only an owner can grant the owner role or change an owner's role.")


def check_can_remove_member(actor_role: str, target_role: str) -> None:
    """Only an owner can remove an owner. An admin can remove admins and members."""
    check_can_manage_team(actor_role)
    if target_role == "owner" and actor_role != "owner":
        raise PermissionError("Only an owner can remove an owner.")


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