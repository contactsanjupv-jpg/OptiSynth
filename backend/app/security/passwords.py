"""
Password hashing using PBKDF2-HMAC-SHA256, entirely from Python's standard
library `hashlib` -- deliberately not bcrypt/argon2/passlib, which are not
installable in every deployment target this codebase might run in. PBKDF2
is a NIST-approved (SP 800-132) password hashing scheme and is a reasonable
MVP choice; if bcrypt/argon2 become available in your environment, this is
the only module that needs to change to adopt them (the User model stores
an opaque `password_hash` string, not a PBKDF2-specific format).

Design:
  - A random 16-byte salt is generated per password (never reused).
  - A server-side pepper (PASSWORD_PEPPER, from settings/env) is mixed in
    in addition to the per-user salt -- this means even a full database
    leak is not enough to brute-force passwords offline without also
    having the pepper, which lives only in the environment.
  - 600,000 iterations follows OWASP's 2023 PBKDF2-SHA256 recommendation.
  - The stored format is "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"
    so the iteration count can be increased later without invalidating
    already-stored hashes (verify_password reads the iteration count back
    out of the stored string).
"""
import hashlib
import hmac
import secrets

from backend.app.config.settings import settings

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16


def hash_password(plaintext_password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _derive(plaintext_password, salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(plaintext_password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_str, salt_hex, digest_hex = stored_hash.split("$")
    except ValueError:
        return False
    if algo != _ALGO:
        return False
    iterations = int(iterations_str)
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = _derive(plaintext_password, salt, iterations)
    return hmac.compare_digest(actual, expected)


def _derive(plaintext_password: str, salt: bytes, iterations: int) -> bytes:
    peppered = plaintext_password.encode("utf-8") + settings.PASSWORD_PEPPER.encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", peppered, salt, iterations)
