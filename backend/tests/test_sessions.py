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
        # Tamper a character in the MIDDLE of the token. (Do not tamper only the LAST
        # character: the last base64 character of the signature carries just 4 data bits
        # plus 2 padding bits, so swapping e.g. 'A' for 'B' changes only padding, decodes
        # to the same signature, and is -- correctly -- still accepted. The earlier form
        # of this test therefore failed about 1 run in 16.)
        mid = len(token) // 2
        tampered = token[:mid] + ("A" if token[mid] != "A" else "B") + token[mid + 1:]
        self.assertIsNone(read_session_token(tampered))

    def test_tampering_is_rejected_for_many_distinct_tokens(self):
        # 300 genuinely different tokens (different payloads => different signatures):
        # changing any data-bearing character must always invalidate the token.
        for user_id in range(1, 301):
            token = create_session_token(user_id=user_id, organization_id=7)
            self.assertIsNotNone(read_session_token(token))
            for i in (5, len(token) // 2, len(token) - 5):
                tampered = token[:i] + ("A" if token[i] != "A" else "B") + token[i + 1:]
                self.assertIsNone(read_session_token(tampered), msg=f"user {user_id}, index {i}")

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
