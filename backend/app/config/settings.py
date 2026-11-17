"""
Centralized configuration. Every environment-dependent value (secrets,
database location, cookie flags) is read here and nowhere else -- if you
find yourself typing os.environ.get(...) in a route or service file, it
belongs here instead.
"""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


MIN_PRODUCTION_SECRET_LENGTH = 32


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set. Copy .env.example to .env and fill it in -- "
            f"the app refuses to start with missing secrets rather than falling "
            f"back to an insecure default."
        )
    return value


class Settings:
    def __init__(self):
        self.SECRET_KEY = _require("SECRET_KEY")
        self.PASSWORD_PEPPER = _require("PASSWORD_PEPPER")

        default_sqlite_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "data", "rdopt.db"
        )
        self.DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{default_sqlite_path}")

        self.SESSION_COOKIE_NAME = os.environ.get("SESSION_COOKIE_NAME", "rdopt_session")
        self.SESSION_LIFETIME_HOURS = int(os.environ.get("SESSION_LIFETIME_HOURS", "12"))
        self.SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"

        self.CORS_ALLOWED_ORIGIN = os.environ.get("CORS_ALLOWED_ORIGIN", "http://localhost:3000")

        self.MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
        self.MAX_UPLOAD_ROWS = int(os.environ.get("MAX_UPLOAD_ROWS", "20000"))

        # Reading an uploaded .xlsx/.docx/.pdf runs in a separate, resource-limited process (see
        # services/sandboxed_parse.py): a small file can expand into gigabytes while it is parsed.
        self.PARSE_ISOLATION = os.environ.get("PARSE_ISOLATION", "true").lower() != "false"
        self.PARSE_TIMEOUT_SECONDS = int(os.environ.get("PARSE_TIMEOUT_SECONDS", "30"))
        self.PARSE_MEMORY_MB = int(os.environ.get("PARSE_MEMORY_MB", "2048"))
        self.MAX_CONCURRENT_PARSES = int(os.environ.get("MAX_CONCURRENT_PARSES", "2"))
        # The most the parent will accept back from a parser process. The largest table the row and column limits allow
        # measured 17.7 MB; this is about twice that. A larger answer means a huge cell or a misbehaving parser.
        self.PARSE_MAX_OUTPUT_MB = int(os.environ.get("PARSE_MAX_OUTPUT_MB", "32"))
        self.MAX_ARCHIVE_UNCOMPRESSED_BYTES = int(os.environ.get("MAX_ARCHIVE_UNCOMPRESSED_BYTES", str(100 * 1024 * 1024)))
        self.MAX_ARCHIVE_ENTRIES = int(os.environ.get("MAX_ARCHIVE_ENTRIES", "2000"))

        self.PORT = int(os.environ.get("PORT", "5050"))
        self.ENV = os.environ.get("ENV", "development")

        # D1: SESSION_COOKIE_SECURE defaults to false for local development. In
        # production that would serve the session cookie without the Secure
        # flag, so refuse to start rather than run that way silently.
        if self.is_production and not self.SESSION_COOKIE_SECURE:
            raise ConfigError(
                "SESSION_COOKIE_SECURE must be true when ENV=production -- refusing "
                "to start with session cookies missing the Secure flag over what "
                "should be HTTPS-only traffic. Set SESSION_COOKIE_SECURE=true."
            )

        # Secrets that are accepted but guessable defeat the point of requiring them.
        if self.is_production:
            for name, value in (("SECRET_KEY", self.SECRET_KEY), ("PASSWORD_PEPPER", self.PASSWORD_PEPPER)):
                if len(value) < MIN_PRODUCTION_SECRET_LENGTH:
                    raise ConfigError(
                        f"{name} is only {len(value)} characters; with ENV=production it must be at least "
                        f"{MIN_PRODUCTION_SECRET_LENGTH}. Generate one with: "
                        "python3 -c \"import secrets; print(secrets.token_hex(32))\""
                    )
            if self.SECRET_KEY == self.PASSWORD_PEPPER:
                raise ConfigError("SECRET_KEY and PASSWORD_PEPPER must be different values.")
        elif self.looks_like_a_deployment:
            logging.getLogger(__name__).warning(
                "ENV is %r but this looks like a deployment (%s). Outside ENV=production, error responses include "
                "internal exception text and the API documentation pages are public. Set ENV=production.",
                self.ENV, "PostgreSQL database" if not self.DATABASE_URL.startswith("sqlite") else "non-local CORS origin")

    @property
    def looks_like_a_deployment(self) -> bool:
        """True when the configuration points at a real database or a public frontend origin."""
        local = self.CORS_ALLOWED_ORIGIN.startswith(("http://localhost", "http://127.0.0.1"))
        return (not self.DATABASE_URL.startswith("sqlite")) or not local

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"


settings = Settings()