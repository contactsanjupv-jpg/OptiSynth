"""
Centralized configuration. Every environment-dependent value (secrets,
database location, cookie flags) is read here and nowhere else -- if you
find yourself typing os.environ.get(...) in a route or service file, it
belongs here instead.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
load_dotenv(dotenv_path=_env_path, override=False)


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

        self.PORT = int(os.environ.get("PORT", "5050"))
        self.ENV = os.environ.get("ENV", "development")

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"


settings = Settings()