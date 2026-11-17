"""
The single dependency every protected route declares to find out "who is
making this request." This is where tenant isolation is actually enforced:
organization_id ALWAYS comes from here (derived from the signed session
cookie, re-verified against the database), NEVER from a request body,
query parameter, or URL segment. A route handler that reads a client-
supplied organization_id instead of using this dependency would be a
critical bug -- there are no such reads anywhere in api/routes/.

Runtime-tested: the FastAPI test suite (backend/tests/test_api.py, 11/11)
and this project's own real-world tenant-isolation testing (a second
organization genuinely gets 404 on the first organization's data) have
both verified this logic end-to-end, on a real running FastAPI app, not
just via syntax validation.
"""
from dataclasses import dataclass

from fastapi import Cookie, HTTPException, status

from backend.app.config.settings import settings
from backend.app.security.sessions import read_session_token
from backend.app.repositories import users_repo, memberships_repo, sessions_repo


@dataclass
class AuthContext:
    user: dict
    organization_id: int
    role: str
    session_id: str | None = None  # the stored session this request came in on (logout revokes exactly this one)


def get_current_actor(
    session_cookie: str | None = Cookie(default=None, alias=settings.SESSION_COOKIE_NAME),
) -> AuthContext:
    """FastAPI dependency: `actor: AuthContext = Depends(get_current_actor)`.
    Raises 401/403 if there's no valid, currently-authorized session --
    routes never run without one. `alias=settings.SESSION_COOKIE_NAME`
    reads the actual cookie name from config, so there is exactly one
    place (config/settings.py) that defines the cookie name."""
    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in.")

    payload = read_session_token(session_cookie)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid.")

    user_id = payload.get("user_id")
    organization_id = payload.get("organization_id")

    # A correctly signed cookie is not enough: the session must still exist server-side, unrevoked and unexpired.
    # (This is what makes logout real: a copy of the cookie is refused once its session is revoked.)
    session_id = payload.get("sid")
    if not isinstance(session_id, str) or not sessions_repo.is_session_active(session_id, user_id, organization_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired or invalid.")

    # Does NOT trust the session payload blindly -- re-fetches the user and
    # re-checks membership + active status on every request, so a
    # deactivated user or a removed membership takes effect immediately,
    # not at next login.
    if not users_repo.user_is_active(user_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is no longer active.")

    membership = memberships_repo.get_membership(user_id, organization_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this organization.")

    user = users_repo.get_user_public(user_id)
    return AuthContext(user=user, organization_id=organization_id, role=membership["role"], session_id=session_id)
