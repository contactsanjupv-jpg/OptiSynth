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
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from backend.app.config.settings import settings

_SALT = "rdopt-session-v1"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=_SALT)


def create_session_token(user_id: int, organization_id: int) -> str:
    return _serializer().dumps({"user_id": user_id, "organization_id": organization_id})


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
