"""
Pydantic request/response schemas for auth endpoints.
"""
from pydantic import BaseModel, EmailStr, Field, field_validator

_MIN_PASSWORD_LENGTH = 10


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=_MIN_PASSWORD_LENGTH, max_length=200)
    # Optional because there are two signup paths: creating a brand new
    # organization (organization_name required) or accepting a team invite
    # (invite_token required instead). Exactly one must resolve to a usable
    # path -- enforced in services/auth_service.py, not here.
    organization_name: str | None = Field(default=None, max_length=200)
    invite_token: str | None = Field(default=None, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)

    @field_validator("organization_name")
    @classmethod
    def strip_org_name(cls, v: str | None) -> str | None:
        return v.strip() if v else v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserPublic(BaseModel):
    id: int
    email: str
    display_name: str | None
    is_active: bool
    created_at: str


class OrganizationPublic(BaseModel):
    id: int
    name: str
    created_at: str


class SignupResponse(BaseModel):
    user: UserPublic
    organization: OrganizationPublic


class MembershipPublic(BaseModel):
    organization_id: int
    organization_name: str
    role: str


class LoginResponse(BaseModel):
    user: UserPublic
    memberships: list[MembershipPublic]


class MeResponse(BaseModel):
    user: UserPublic
    organization_id: int
    role: str