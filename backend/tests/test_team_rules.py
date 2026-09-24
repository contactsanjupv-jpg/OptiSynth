"""
Run with: python3 -m unittest backend.tests.test_team_rules -v
"""
import unittest
from datetime import datetime, timedelta, timezone

from backend.app.services.team_rules import (
    check_can_manage_team, check_not_removing_last_owner, check_invitation_usable,
)
from backend.app.schemas.errors import ValidationError


class TestCanManageTeam(unittest.TestCase):
    def test_owner_can_manage(self):
        check_can_manage_team("owner")

    def test_admin_can_manage(self):
        check_can_manage_team("admin")

    def test_member_cannot_manage(self):
        with self.assertRaises(PermissionError):
            check_can_manage_team("member")


class TestLastOwnerProtection(unittest.TestCase):
    def test_blocks_removing_only_owner(self):
        with self.assertRaises(ValidationError):
            check_not_removing_last_owner("owner", owner_count=1)

    def test_allows_removing_owner_when_others_exist(self):
        check_not_removing_last_owner("owner", owner_count=2)

    def test_allows_removing_non_owner_regardless_of_owner_count(self):
        check_not_removing_last_owner("member", owner_count=1)
        check_not_removing_last_owner("admin", owner_count=1)


class TestInvitationUsable(unittest.TestCase):
    def _invite(self, status="pending", expires_in_days=7):
        expires = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        return {"status": status, "expires_at": expires.isoformat()}

    def test_pending_unexpired_invitation_is_usable(self):
        check_invitation_usable(self._invite())

    def test_revoked_invitation_rejected(self):
        with self.assertRaises(ValidationError):
            check_invitation_usable(self._invite(status="revoked"))

    def test_already_accepted_invitation_rejected(self):
        with self.assertRaises(ValidationError):
            check_invitation_usable(self._invite(status="accepted"))

    def test_expired_invitation_rejected(self):
        with self.assertRaises(ValidationError):
            check_invitation_usable(self._invite(expires_in_days=-1))

    def test_error_message_does_not_distinguish_expired_from_revoked(self):
        try:
            check_invitation_usable(self._invite(status="revoked"))
        except ValidationError as e:
            revoked_msg = e.message
        try:
            check_invitation_usable(self._invite(expires_in_days=-1))
        except ValidationError as e:
            expired_msg = e.message
        self.assertEqual(revoked_msg, expired_msg)


if __name__ == "__main__":
    unittest.main()