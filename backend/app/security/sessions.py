"""
Session token signing, using `itsdangerous` (a small, focused signing
library -- the same one Flask itself uses internally for its session
cookie, used here directly since we're on FastAPI). This is NOT a JWT: the
payload is opaque to the client, carries a server-checked expiry, and is
verified against SECRET_KEY on every request.

The signed token is stored in an httpOnly cookie (set in
api/routes/auth_routes.py). JavaScript cannot read it (mitigates XSS token
theft), and it is authenticated (HMAC-signed) so the browser cannot forge
or tamper with it -- attempting to modify the payload invalidates the
signature and read_session_token() returns None.

The payload itself (user_id, organization_id) is still NOT trusted blindly
downstream -- api/dependencies.py#get_current_actor re-fetches the user and
re-checks organization membership from the database on every request, so a
deactivated user or revoked membership loses access immediately rather than
at next login.
"""
import secrets
from datetime import datetime, timedelta, timezone

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from backend.app.config.settings import settings
from backend.app.repositories import sessions_repo

_SALT = "rdopt-session-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_SALT)


def create_session_token(user_id: int, organization_id: int, session_id: str | None = None) -> str:
    """Signs a token. Pure (no database). Real sessions are started with start_session(), which also stores them."""
    payload = {"user_id": user_id, "organization_id": organization_id}
    if session_id is not None:
        payload["sid"] = session_id
    return _serializer().dumps(payload)


def start_session(user_id: int, organization_id: int) -> str:
    """Stores a new server-side session (repositories/sessions_repo.py) and returns the signed token that carries its
    random id. The API accepts the token only while that stored session is unrevoked and unexpired (see
    api/dependencies.py), which is what lets logout really end it. A token without a session id (issued before server-side
    sessions existed) is refused there."""
    session_id = secrets.token_urlsafe(24)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_LIFETIME_HOURS)).isoformat()
    sessions_repo.create_session(session_id, user_id, organization_id, expires_at)
    return create_session_token(user_id, organization_id, session_id)


def read_session_token(token: str) -> dict | None:
    """Returns {"user_id": ..., "organization_id": ...} if the token is
    validly signed and not expired, else None. Never raises -- callers
    (api/dependencies.py) treat None as "not authenticated," not as an
    error, so a corrupted or expired cookie behaves the same as no cookie."""
    max_age_seconds = settings.SESSION_LIFETIME_HOURS * 3600
    try:
        return _serializer().loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
