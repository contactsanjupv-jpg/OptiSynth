"""
LEGACY / SERVICE-INTEGRATION AUTH PATH.

This module is the original MVP's authentication mechanism: a long-lived
API key, presented via the X-API-Key header, verified against a keyed hash.
It is kept ONLY for machine-to-machine / integration use cases (e.g. a
customer's own script pulling recommendations via the API) and is
intentionally isolated here so it is never confused with the primary
browser session auth in security/sessions.py + security/passwords.py.

The frontend web app does NOT use this path. See
backend/app/api/dependencies.py -- browser requests authenticate via the
signed session cookie; API-key requests (if ever exposed) would go through
a separate, clearly-labeled route group.

Design (unchanged from the original MVP, still sound):
  - The plaintext key is shown to the customer exactly once, at creation
    time, and is never stored anywhere -- only a keyed hash (HMAC-SHA256
    with a server-side pepper from the environment) is persisted. Even
    full read access to the database does not let an attacker recover a
    working key.
"""
import os
import hmac
import hashlib
import secrets

API_KEY_PEPPER_ENV_VAR = "API_KEY_PEPPER"


def _get_pepper() -> bytes:
    pepper = os.environ.get(API_KEY_PEPPER_ENV_VAR)
    if not pepper:
        raise RuntimeError(
            f"{API_KEY_PEPPER_ENV_VAR} is not set. Required only if the legacy API-key "
            f"integration path is enabled -- set it in .env before using that path."
        )
    return pepper.encode("utf-8")


def generate_api_key() -> str:
    """Returns a new plaintext API key. Show this to the customer once; never log it."""
    return "rdopt_" + secrets.token_urlsafe(32)


def hash_api_key(plaintext_key: str) -> str:
    return hmac.new(_get_pepper(), plaintext_key.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_api_key(plaintext_key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(plaintext_key), stored_hash)
