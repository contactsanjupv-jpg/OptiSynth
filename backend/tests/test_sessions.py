"""
Real, executable tests for session token signing -- itsdangerous is
available in the sandbox and this module has no FastAPI dependency, so
this genuinely runs (unlike the FastAPI route tests).
Run with: python3 -m unittest backend.tests.test_sessions -v
"""
import os
import time
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-for-session-tests")
os.environ.setdefault("PASSWORD_PEPPER", "test-pepper-for-session-tests")

from backend.app.security.sessions import create_session_token, read_session_token


class TestSessionTokens(unittest.TestCase):
    def test_round_trip(self):
        token = create_session_token(user_id=42, organization_id=7)
        payload = read_session_token(token)
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["organization_id"], 7)

    def test_tampered_token_rejected(self):
        token = create_session_token(user_id=42, organization_id=7)
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        self.assertIsNone(read_session_token(tampered))

    def test_garbage_token_rejected(self):
        self.assertIsNone(read_session_token("not-a-real-token"))

    def test_empty_token_rejected(self):
        self.assertIsNone(read_session_token(""))

    def test_token_does_not_expire_immediately(self):
        token = create_session_token(user_id=1, organization_id=1)
        time.sleep(0.1)
        self.assertIsNotNone(read_session_token(token))


if __name__ == "__main__":
    unittest.main()
