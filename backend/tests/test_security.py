"""
Run with: python3 -m unittest backend.tests.test_security -v
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PASSWORD_PEPPER", "test-pepper")
# API_KEY_PEPPER is deliberately NOT set here with setdefault(): see TestApiKeyHashing.setUpClass.

from backend.app.security.passwords import hash_password, verify_password
from backend.app.security.api_keys import generate_api_key, hash_api_key, verify_api_key


class TestPasswordHashing(unittest.TestCase):
    def test_correct_password_verifies(self):
        h = hash_password("correct-horse-battery-staple")
        self.assertTrue(verify_password("correct-horse-battery-staple", h))

    def test_wrong_password_fails(self):
        h = hash_password("correct-horse-battery-staple")
        self.assertFalse(verify_password("wrong-password", h))

    def test_same_password_hashes_differently_each_time(self):
        # different random salt each call -- prevents identical passwords
        # from producing identical hashes (which would leak that two users
        # share a password just by comparing hashes)
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        self.assertNotEqual(h1, h2)
        self.assertTrue(verify_password("same-password", h1))
        self.assertTrue(verify_password("same-password", h2))

    def test_malformed_hash_fails_closed(self):
        self.assertFalse(verify_password("anything", "not-a-real-hash"))

    def test_hash_does_not_contain_plaintext(self):
        h = hash_password("super-secret-value-xyz")
        self.assertNotIn("super-secret-value-xyz", h)


class TestApiKeyHashing(unittest.TestCase):
    # These tests need a pepper, and they get a TEST-ONLY one that is set explicitly for this
    # class and restored afterwards. It is not set with os.environ.setdefault(): a developer's
    # .env copied from .env.example contains `API_KEY_PEPPER=` (an EMPTY value), and an empty
    # value still counts as "already set", so setdefault would leave it empty and the tests
    # would fail -- but only when another module has already loaded settings.py (which loads
    # .env) before this module is imported, i.e. only in a combined run. Overriding here makes
    # the result independent of import order and of whatever the real .env contains.
    # The API-key implementation itself is unchanged and still refuses an empty pepper.
    @classmethod
    def setUpClass(cls):
        cls._env_patch = mock.patch.dict(os.environ, {"API_KEY_PEPPER": "test-api-key-pepper"})
        cls._env_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.stop()

    def test_an_empty_or_missing_pepper_is_still_refused(self):
        # Guards the fix above against weakening the implementation: hashing must keep failing
        # closed when no real pepper is configured.
        for value in ("", None):
            env = {"API_KEY_PEPPER": value} if value is not None else {}
            with mock.patch.dict(os.environ, env):
                if value is None:
                    os.environ.pop("API_KEY_PEPPER", None)
                with self.assertRaises(RuntimeError):
                    hash_api_key("rdopt_anything")

    def test_generated_key_has_expected_prefix(self):
        key = generate_api_key()
        self.assertTrue(key.startswith("rdopt_"))

    def test_correct_key_verifies(self):
        key = generate_api_key()
        h = hash_api_key(key)
        self.assertTrue(verify_api_key(key, h))

    def test_wrong_key_fails(self):
        key = generate_api_key()
        h = hash_api_key(key)
        self.assertFalse(verify_api_key(generate_api_key(), h))

    def test_hash_does_not_contain_plaintext_key(self):
        key = generate_api_key()
        h = hash_api_key(key)
        self.assertNotIn(key, h)


if __name__ == "__main__":
    unittest.main()
