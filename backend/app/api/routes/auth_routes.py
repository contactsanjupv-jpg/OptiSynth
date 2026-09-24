"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi is not installed here. Syntax-
validated via py_compile only -- see README "Sandbox limitations".
"""
from fastapi import APIRouter, Depends, Response

from backend.app.config.settings import settings
from backend.app.schemas.auth_schemas import (
    SignupRequest, LoginRequest, SignupResponse, LoginResponse, MeResponse,
)
from backend.app.services import auth_service, audit_service
from backend.app.security.sessions import create_session_token
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: int, organization_id: int) -> None:
    token = create_session_token(user_id, organization_id)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=settings.SESSION_LIFETIME_HOURS * 3600,
    )


@router.post("/signup", response_model=SignupResponse, status_code=201)
def signup(body: SignupRequest, response: Response):
    result = auth_service.sign_up(body)
    user_id = result["user"]["id"]
    org_id = result["organization"]["id"]
    _set_session_cookie(response, user_id, org_id)
    audit_service.log(org_id, user_id, "auth.signup")
    return result


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, response: Response):
    result = auth_service.log_in(body)
    user_id = result["user"]["id"]
    org_id = result["memberships"][0]["organization_id"]
    _set_session_cookie(response, user_id, org_id)
    audit_service.log(org_id, user_id, "auth.login")
    return result


@router.post("/logout")
def logout(response: Response, actor: AuthContext = Depends(get_current_actor)):
    audit_service.log(actor.organization_id, actor.user["id"], "auth.logout")
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(actor: AuthContext = Depends(get_current_actor)):
    return {"user": actor.user, "organization_id": actor.organization_id, "role": actor.role}
