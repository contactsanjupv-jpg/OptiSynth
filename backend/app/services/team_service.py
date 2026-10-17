import os

from backend.app.repositories import memberships_repo, invitations_repo, users_repo, organizations_repo
from backend.app.schemas.errors import ValidationError
from backend.app.schemas.team_schemas import InvitationPreviewResponse, InviteMemberRequest, UpdateMemberRoleRequest
from backend.app.services.team_rules import (
    check_can_manage_team, check_can_invite_with_role, check_can_change_role, check_can_remove_member,
    check_invitation_usable,
)

# There is no email-sending integration in this MVP -- invite_member()
# returns a shareable link built from this base URL; it is never emailed
# automatically. This is a public URL, not a secret.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")


def list_members(organization_id: int) -> list:
    return memberships_repo.list_members_for_organization(organization_id)


def invite_member(organization_id: int, actor_role: str, actor_user_id: int, body: InviteMemberRequest) -> dict:
    check_can_invite_with_role(actor_role, body.role)

    existing_user = users_repo.get_user_by_email(body.email)
    if existing_user:
        existing_membership = memberships_repo.get_membership(existing_user["id"], organization_id)
        if existing_membership:
            raise ValidationError("This person is already a member of your organization.", field="email")

    result = invitations_repo.create_invitation(
        organization_id, body.email, body.role, invited_by_user_id=actor_user_id
    )
    invite_url = f"{FRONTEND_BASE_URL}/login?invite={result['token']}"
    return {
        "id": result["id"],
        "email": body.email,
        "role": body.role,
        "status": "pending",
        "expires_at": result["expires_at"],
        "invite_url": invite_url,
    }


def list_pending_invitations(organization_id: int, actor_role: str) -> list:
    check_can_manage_team(actor_role)
    invitations = invitations_repo.list_pending_invitations(organization_id)
    for inv in invitations:
        inv["invite_url"] = f"{FRONTEND_BASE_URL}/login?invite={inv['token']}"
    return invitations


def revoke_invitation(organization_id: int, actor_role: str, invitation_id: int) -> None:
    check_can_manage_team(actor_role)
    revoked = invitations_repo.revoke_invitation(organization_id, invitation_id)
    if not revoked:
        raise LookupError("Invitation not found.")


_LAST_OWNER_MESSAGE = (
    "Can't remove or demote the last owner of an organization. Promote another member to owner first."
)


def remove_member(organization_id: int, actor_role: str, target_user_id: int) -> None:
    check_can_manage_team(actor_role)
    membership = memberships_repo.get_membership(target_user_id, organization_id)
    if not membership:
        raise LookupError("Member not found.")
    check_can_remove_member(actor_role, membership["role"])
    outcome = memberships_repo.remove_membership_keeping_an_owner(organization_id, target_user_id)
    if outcome == "last_owner":
        raise ValidationError(_LAST_OWNER_MESSAGE)
    if outcome == "not_found":
        raise LookupError("Member not found.")


def update_member_role(organization_id: int, actor_role: str, actor_user_id: int, target_user_id: int,
                       body: UpdateMemberRoleRequest) -> None:
    check_can_manage_team(actor_role)
    membership = memberships_repo.get_membership(target_user_id, organization_id)
    if not membership:
        raise LookupError("Member not found.")
    check_can_change_role(actor_role, actor_user_id, target_user_id, membership["role"], body.role)
    outcome = memberships_repo.update_membership_role_keeping_an_owner(organization_id, target_user_id, body.role)
    if outcome == "last_owner":
        raise ValidationError(_LAST_OWNER_MESSAGE)
    if outcome == "not_found":
        raise LookupError("Member not found.")


def get_invitation_preview(token: str) -> InvitationPreviewResponse:
    """
    Public, unauthenticated preview for the invite link (GET /api/invitations/{token}).
    Used by the login/signup page before the invitee has any session.
    """
    invitation = invitations_repo.get_invitation_by_token(token)
    if invitation is None:
        raise ValidationError("This invite link is no longer valid.")

    organization = organizations_repo.get_organization(invitation["organization_id"])
    if organization is None:
        raise ValidationError("This invite link is no longer valid.")

    return InvitationPreviewResponse(
        organization_name=organization["name"],
        email=invitation["email"],
        role=invitation["role"],
        status=invitation["status"],
        expires_at=invitation["expires_at"],
    )