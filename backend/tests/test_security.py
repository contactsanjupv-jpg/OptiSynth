"""
Run with: python3 -m unittest backend.tests.test_security -v
"""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PASSWORD_PEPPER", "test-pepper")
os.environ.setdefault("API_KEY_PEPPER", "test-api-key-pepper")

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
