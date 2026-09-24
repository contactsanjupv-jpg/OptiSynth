"""
Pydantic v2 schemas for the Team management feature.

Covers: inviting members, listing members, updating member roles,
removing members, and the public invitation-preview flow used by
the signup-via-invite page.
"""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, EmailStr, Field


Role = Literal["owner", "admin", "member"]
InvitationStatus = Literal["pending", "accepted", "revoked"]


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: Role = "member"


class UpdateMemberRoleRequest(BaseModel):
    role: Role


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class MemberResponse(BaseModel):
    user_id: int
    email: EmailStr
    full_name: str | None = None
    role: Role
    joined_at: datetime

    model_config = {"from_attributes": True}


class InvitationResponse(BaseModel):
    id: int
    organization_id: int
    email: EmailStr
    role: Role
    status: InvitationStatus
    invited_by_user_id: int
    created_at: datetime
    expires_at: datetime
    invite_url: str | None = Field(
        default=None,
        description="Shareable signup-with-invite link. Populated only "
        "when the invitation is first created, since the raw token "
        "is not re-exposed on later reads.",
    )

    model_config = {"from_attributes": True}


class InvitationPreviewResponse(BaseModel):
    """
    Public, unauthenticated preview shown on the login/signup page when
    someone opens an invite link (GET /api/invitations/{token}).
    Deliberately minimal — no organization_id, no internal ids beyond
    what's needed to render 'You've been invited to join <org> as <role>'.
    """
    organization_name: str
    email: EmailStr
    role: Role
    status: InvitationStatus
    expires_at: datetime