"""
NOT RUNTIME-TESTED IN THE SANDBOX: fastapi is not installed here. Syntax-
validated via py_compile only -- see README "Sandbox limitations".
"""
from fastapi import APIRouter, Depends, Request, Response

from backend.app.config.settings import settings
from backend.app.schemas.auth_schemas import (
    SignupRequest, LoginRequest, SignupResponse, LoginResponse, MeResponse, ProfileUpdateRequest, PasswordChangeRequest,
)
from backend.app.services import auth_service, audit_service
from backend.app.schemas.errors import Forbidden, TooManyAttempts, ValidationError
from backend.app.security import login_throttle
from backend.app.repositories import sessions_repo
from backend.app.security.sessions import start_session
from backend.app.api.dependencies import get_current_actor, AuthContext

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, user_id: int, organization_id: int, request: Request) -> None:
    token = start_session(user_id, organization_id, request.headers.get("user-agent"),
                          request.client.host if request.client else None)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        max_age=settings.SESSION_LIFETIME_HOURS * 3600,
    )


@router.post("/signup", response_model=SignupResponse, status_code=201)
def signup(body: SignupRequest, request: Request, response: Response):
    if not settings.ALLOW_PUBLIC_SIGNUP and not body.invite_token:
        raise Forbidden("Sign-up is by invitation only. Ask your administrator for an invitation.")
    result = auth_service.sign_up(body)
    user_id = result["user"]["id"]
    org_id = result["organization"]["id"]
    _set_session_cookie(response, user_id, org_id, request)
    audit_service.log(org_id, user_id, "auth.signup")
    return result


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response):
    address = request.client.host if request.client else "unknown"
    wait = login_throttle.throttle.retry_after(address, body.email)
    if wait:  # refused before any password work is done, so guessing a locked account costs the server nothing
        raise TooManyAttempts(wait, "Too many sign-in attempts. Please wait a few minutes and try again.")
    try:
        result = auth_service.log_in(body)
    except ValidationError:
        login_throttle.throttle.record_failure(address, body.email)
        raise
    login_throttle.throttle.record_success(address, body.email)
    user_id = result["user"]["id"]
    org_id = result["memberships"][0]["organization_id"]
    _set_session_cookie(response, user_id, org_id, request)
    audit_service.log(org_id, user_id, "auth.login")
    return result


@router.post("/logout")
def logout(response: Response, actor: AuthContext = Depends(get_current_actor)):
    audit_service.log(actor.organization_id, actor.user["id"], "auth.logout")
    sessions_repo.revoke_session(actor.session_id)  # server-side: a copy of the cookie stops working too
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(actor: AuthContext = Depends(get_current_actor)):
    return {"user": actor.user, "organization_id": actor.organization_id, "role": actor.role}


@router.get("/config")
def public_config():
    """What the sign-in page needs before anyone is signed in."""
    return {"public_signup": settings.ALLOW_PUBLIC_SIGNUP}


@router.patch("/profile")
def update_profile(body: ProfileUpdateRequest, actor: AuthContext = Depends(get_current_actor)):
    user = auth_service.update_profile(actor.user["id"], body.display_name)
    audit_service.log(actor.organization_id, actor.user["id"], "auth.profile_update")
    return {"user": user}


@router.post("/password")
def change_password(body: PasswordChangeRequest, request: Request, actor: AuthContext = Depends(get_current_actor)):
    """Needs the current password. A wrong guess counts toward the same limit as sign-in, then every other session ends."""
    address = request.client.host if request.client else "unknown"
    email = actor.user["email"]
    wait = login_throttle.throttle.retry_after(address, email)
    if wait:
        raise TooManyAttempts(wait, "Too many attempts. Please wait a few minutes and try again.")
    try:
        auth_service.change_password(actor.user["id"], body.current_password, body.new_password)
    except ValidationError as err:
        if err.field == "current_password":
            login_throttle.throttle.record_failure(address, email)
        raise
    ended = sessions_repo.revoke_all_for_user(actor.user["id"], except_session_id=actor.session_id)
    audit_service.log(actor.organization_id, actor.user["id"], "auth.password_change", detail=f"other_sessions_ended={ended}")
    return {"ok": True, "other_sessions_ended": ended}


def _describe_agent(agent) -> str:
    text = agent or ""
    browser = next((n for n in ("Edg", "Chrome", "Firefox", "Safari") if n in text), None)
    system = next((n for n in ("Windows", "Mac OS", "iPhone", "Android", "Linux") if n in text), None)
    names = {"Edg": "Edge"}
    parts = [names.get(browser, browser), system]
    return " on ".join(p for p in parts if p) or "Unknown device"


@router.get("/sessions")
def list_sessions(actor: AuthContext = Depends(get_current_actor)):
    rows = sessions_repo.list_active_for_user(actor.user["id"])
    return [{"id": r["id"], "device": _describe_agent(r["user_agent"]), "address": r["ip_address"],
             "created_at": r["created_at"], "expires_at": r["expires_at"], "current": r["session_id"] == actor.session_id}
            for r in rows]


@router.delete("/sessions/{row_id}")
def revoke_session(row_id: int, response: Response, actor: AuthContext = Depends(get_current_actor)):
    mine = {r["id"]: r for r in sessions_repo.list_active_for_user(actor.user["id"])}
    if row_id not in mine or not sessions_repo.revoke_by_id(actor.user["id"], row_id):
        raise LookupError("Session not found.")
    audit_service.log(actor.organization_id, actor.user["id"], "auth.session_revoke")
    if mine[row_id]["session_id"] == actor.session_id:
        response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return {"ok": True}


@router.post("/sessions/revoke-others")
def revoke_other_sessions(actor: AuthContext = Depends(get_current_actor)):
    ended = sessions_repo.revoke_all_for_user(actor.user["id"], except_session_id=actor.session_id)
    audit_service.log(actor.organization_id, actor.user["id"], "auth.sessions_revoke_others", detail=f"ended={ended}")
    return {"ended": ended}


@router.post("/logout-all")
def logout_everywhere(response: Response, actor: AuthContext = Depends(get_current_actor)):
    ended = sessions_repo.revoke_all_for_user(actor.user["id"])
    audit_service.log(actor.organization_id, actor.user["id"], "auth.logout_all", detail=f"ended={ended}")
    response.delete_cookie(settings.SESSION_COOKIE_NAME)
    return {"ended": ended}
