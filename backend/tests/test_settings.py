"""
D1 -- production session-cookie guard. Constructs Settings() DIRECTLY (never the
module-level singleton) so several configurations can be tested safely.
Run:  python3 -m unittest backend.tests.test_settings -v
"""
import os
import unittest
from unittest import mock

# settings.py builds its module-level singleton at import time and needs these.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.config.settings import Settings, ConfigError  # noqa: E402

BASE = {"SECRET_KEY": "k" * 20, "PASSWORD_PEPPER": "p" * 20}


def build(**env):
    clean = {k: v for k, v in os.environ.items() if k not in ("ENV", "SESSION_COOKIE_SECURE")}
    clean.update(BASE)
    clean.update(env)
    with mock.patch.dict(os.environ, clean, clear=True):
        return Settings()


class TestProductionCookieGuard(unittest.TestCase):
    def test_production_without_secure_cookie_refuses_to_start(self):
        with self.assertRaises(ConfigError) as cm:
            build(ENV="production")
        self.assertIn("SESSION_COOKIE_SECURE", str(cm.exception))

    def test_production_with_secure_cookie_explicitly_false_refuses(self):
        with self.assertRaises(ConfigError):
            build(ENV="production", SESSION_COOKIE_SECURE="false")

    def test_production_with_anything_but_true_refuses(self):
        for v in ("0", "no", "", "yes", "1"):
            with self.assertRaises(ConfigError, msg=repr(v)):
                build(ENV="production", SESSION_COOKIE_SECURE=v)

    def test_production_with_secure_cookie_true_starts(self):
        s = build(ENV="production", SESSION_COOKIE_SECURE="true")
        self.assertTrue(s.is_production)
        self.assertTrue(s.SESSION_COOKIE_SECURE)

    def test_production_value_is_case_insensitive_true(self):
        self.assertTrue(build(ENV="production", SESSION_COOKIE_SECURE="TRUE").SESSION_COOKIE_SECURE)

    def test_non_production_does_not_require_secure_cookie(self):
        s = build(ENV="development")
        self.assertFalse(s.is_production)
        self.assertFalse(s.SESSION_COOKIE_SECURE)

    def test_env_unset_defaults_to_non_production_and_starts(self):
        s = build()
        self.assertEqual(s.ENV, "development")

    def test_missing_secrets_still_refuse_to_start(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError):
                Settings()


if __name__ == "__main__":
    unittest.main()