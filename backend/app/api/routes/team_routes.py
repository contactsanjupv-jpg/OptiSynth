"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi is not installed here.
"""
from fastapi import APIRouter, Depends

from backend.app.schemas.team_schemas import (
    InviteMemberRequest, UpdateMemberRoleRequest, InvitationPreviewResponse,
)
from backend.app.services import team_service, audit_service
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/team", tags=["team"])

# Public (no auth) -- called from the signup page before the invitee has any session.
public_router = APIRouter(prefix="/api/invitations", tags=["team"])


@router.get("/members")
def list_members(actor: AuthContext = Depends(get_current_actor)):
    return team_service.list_members(actor.organization_id)


@router.post("/invitations", status_code=201)
def invite_member(body: InviteMemberRequest, actor: AuthContext = Depends(get_current_actor)):
    result = team_service.invite_member(actor.organization_id, actor.role, body)
    audit_service.log(actor.organization_id, actor.user["id"], "team.invite", detail=f"role={body.role}")
    return result


@router.get("/invitations")
def list_pending_invitations(actor: AuthContext = Depends(get_current_actor)):
    return team_service.list_pending_invitations(actor.organization_id, actor.role)


@router.delete("/invitations/{invitation_id}")
def revoke_invitation(invitation_id: int, actor: AuthContext = Depends(get_current_actor)):
    team_service.revoke_invitation(actor.organization_id, actor.role, invitation_id)
    audit_service.log(actor.organization_id, actor.user["id"], "team.revoke_invitation")
    return {"ok": True}


@router.delete("/members/{user_id}")
def remove_member(user_id: int, actor: AuthContext = Depends(get_current_actor)):
    team_service.remove_member(actor.organization_id, actor.role, user_id)
    audit_service.log(actor.organization_id, actor.user["id"], "team.remove_member",
                       detail=f"removed_user_id={user_id}")
    return {"ok": True}


@router.patch("/members/{user_id}/role")
def update_member_role(user_id: int, body: UpdateMemberRoleRequest, actor: AuthContext = Depends(get_current_actor)):
    team_service.update_member_role(actor.organization_id, actor.role, user_id, body)
    audit_service.log(actor.organization_id, actor.user["id"], "team.update_role",
                       detail=f"user_id={user_id},role={body.role}")
    return {"ok": True}


@public_router.get("/{token}", response_model=InvitationPreviewResponse)
def preview_invitation(token: str):
    return team_service.get_invitation_preview(token)