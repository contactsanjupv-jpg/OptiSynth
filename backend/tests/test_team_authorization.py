"""
Level 1 -- role management is enforced on the SERVER.

Covers: no owner promotion by admins (invite, role change, self-promotion), owners keep their
workflows, owners can't be demoted/removed by admins, the last-owner rule (including the atomic,
race-free repository guard), members cannot manage anyone, and cross-organization requests.

Run:  python3 -m unittest backend.tests.test_team_authorization -v
"""
import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("PASSWORD_PEPPER", "test-password-pepper-not-for-production")

from backend.app.schemas.errors import ValidationError  # noqa: E402
from backend.app.services import team_rules  # noqa: E402
from backend.tests._api_base import IsolatedApiTestCase  # noqa: E402

PASSWORD = "correct-horse-battery-staple"


class TestRoleRules(unittest.TestCase):
    """The pure rules, independent of HTTP."""

    def test_only_owner_can_invite_an_owner(self):
        team_rules.check_can_invite_with_role("owner", "owner")
        for role in ("admin", "member"):
            team_rules.check_can_invite_with_role("owner", role)
            team_rules.check_can_invite_with_role("admin", role)
        with self.assertRaises(PermissionError):
            team_rules.check_can_invite_with_role("admin", "owner")
        with self.assertRaises(PermissionError):
            team_rules.check_can_invite_with_role("member", "member")

    def test_invite_rejects_unknown_role(self):
        with self.assertRaises(ValidationError):
            team_rules.check_can_invite_with_role("owner", "superuser")

    def test_admin_cannot_grant_owner_or_touch_an_owner(self):
        with self.assertRaises(PermissionError):
            team_rules.check_can_change_role("admin", 1, 2, "member", "owner")
        with self.assertRaises(PermissionError):
            team_rules.check_can_change_role("admin", 1, 2, "owner", "member")
        with self.assertRaises(PermissionError):
            team_rules.check_can_change_role("admin", 1, 2, "owner", "admin")

    def test_admin_can_move_members_and_admins_between_those_two_roles(self):
        team_rules.check_can_change_role("admin", 1, 2, "member", "admin")
        team_rules.check_can_change_role("admin", 1, 2, "admin", "member")

    def test_owner_can_change_any_role_of_someone_else(self):
        for current in ("owner", "admin", "member"):
            for new in ("owner", "admin", "member"):
                team_rules.check_can_change_role("owner", 1, 2, current, new)

    def test_nobody_changes_their_own_role(self):
        for actor in ("owner", "admin"):
            with self.assertRaises(ValidationError) as ctx:
                team_rules.check_can_change_role(actor, 5, 5, actor, "owner")
            self.assertEqual(ctx.exception.field, "role")

    def test_member_cannot_change_roles(self):
        with self.assertRaises(PermissionError):
            team_rules.check_can_change_role("member", 1, 2, "member", "admin")

    def test_only_owner_can_remove_an_owner(self):
        team_rules.check_can_remove_member("owner", "owner")
        team_rules.check_can_remove_member("admin", "admin")
        team_rules.check_can_remove_member("admin", "member")
        with self.assertRaises(PermissionError):
            team_rules.check_can_remove_member("admin", "owner")
        with self.assertRaises(PermissionError):
            team_rules.check_can_remove_member("member", "member")


class TeamBase(IsolatedApiTestCase):
    dir_name = "_test_env_team"
    _email_n = 0

    def _next_email(self, tag):
        type(self)._email_n += 1
        return f"{tag}{type(self)._email_n}@team-test.example.com"

    def new_org(self, label):
        org_id, client = self.org_and_client(label)
        return org_id, client

    def user_id(self, owner_client, email):
        members = owner_client.get("/api/team/members").json()
        return next(m["user_id"] for m in members if m["email"] == email)

    def join(self, inviter_client, role, tag="m"):
        """Invites a new person with `role` and signs them up through the invite. -> (client, user_id, email)."""
        email = self._next_email(tag)
        r = inviter_client.post("/api/team/invitations", json={"email": email, "role": role})
        assert r.status_code == 201, r.text
        token = r.json()["invite_url"].split("invite=")[1]
        client = self.client_factory()
        r = client.post("/api/auth/signup", json={
            "email": email, "password": PASSWORD, "invite_token": token, "display_name": "Invitee"})
        assert r.status_code in (200, 201), r.text
        return client, self.user_id(inviter_client, email), email

    def roles(self, owner_client):
        return {m["email"]: m["role"] for m in owner_client.get("/api/team/members").json()}


class TestInviteAuthorization(TeamBase):
    def test_admin_cannot_invite_an_owner(self):
        _, owner = self.new_org("Inv1")
        admin, _, _ = self.join(owner, "admin", "adm")
        r = admin.post("/api/team/invitations", json={"email": self._next_email("x"), "role": "owner"})
        self.assertEqual(r.status_code, 403, r.text)
        pending = owner.get("/api/team/invitations").json()
        self.assertEqual([i for i in pending if i["role"] == "owner"], [])

    def test_admin_can_invite_admins_and_members(self):
        _, owner = self.new_org("Inv2")
        admin, _, _ = self.join(owner, "admin", "adm")
        for role in ("admin", "member"):
            r = admin.post("/api/team/invitations", json={"email": self._next_email("ok"), "role": role})
            self.assertEqual(r.status_code, 201, r.text)

    def test_owner_can_invite_an_owner(self):
        _, owner = self.new_org("Inv3")
        r = owner.post("/api/team/invitations", json={"email": self._next_email("own"), "role": "owner"})
        self.assertEqual(r.status_code, 201, r.text)

    def test_member_cannot_invite_anyone(self):
        _, owner = self.new_org("Inv4")
        member, _, _ = self.join(owner, "member")
        r = member.post("/api/team/invitations", json={"email": self._next_email("x"), "role": "member"})
        self.assertEqual(r.status_code, 403)

    def test_invitation_records_who_sent_it(self):
        from backend.app.repositories import invitations_repo
        _, owner = self.new_org("Inv5")
        admin, admin_id, _ = self.join(owner, "admin", "adm")
        email = self._next_email("who")
        r = admin.post("/api/team/invitations", json={"email": email, "role": "member"})
        token = r.json()["invite_url"].split("invite=")[1]
        self.assertEqual(invitations_repo.get_invitation_by_token(token)["invited_by_user_id"], admin_id)

    def test_unknown_role_value_is_rejected(self):
        _, owner = self.new_org("Inv6")
        r = owner.post("/api/team/invitations", json={"email": self._next_email("x"), "role": "superuser"})
        self.assertEqual(r.status_code, 422)


class TestRoleChangeAuthorization(TeamBase):
    def test_admin_cannot_promote_a_member_to_owner(self):
        _, owner = self.new_org("Role1")
        admin, _, _ = self.join(owner, "admin", "adm")
        _, member_id, member_email = self.join(owner, "member")
        r = admin.patch(f"/api/team/members/{member_id}/role", json={"role": "owner"})
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.roles(owner)[member_email], "member")

    def test_admin_cannot_promote_themselves(self):
        _, owner = self.new_org("Role2")
        admin, admin_id, admin_email = self.join(owner, "admin", "adm")
        r = admin.patch(f"/api/team/members/{admin_id}/role", json={"role": "owner"})
        self.assertIn(r.status_code, (400, 403), r.text)
        self.assertEqual(self.roles(owner)[admin_email], "admin")

    def test_admin_cannot_demote_or_change_an_owner(self):
        _, owner = self.new_org("Role3")
        admin, _, _ = self.join(owner, "admin", "adm")
        owner_email = next(e for e, r in self.roles(owner).items() if r == "owner")
        owner_id = self.user_id(owner, owner_email)
        self.assertEqual(admin.patch(f"/api/team/members/{owner_id}/role", json={"role": "member"}).status_code, 403)
        self.assertEqual(self.roles(owner)[owner_email], "owner")

    def test_admin_cannot_remove_an_owner(self):
        _, owner = self.new_org("Role4")
        admin, _, _ = self.join(owner, "admin", "adm")
        owner_email = next(e for e, r in self.roles(owner).items() if r == "owner")
        owner_id = self.user_id(owner, owner_email)
        self.assertEqual(admin.delete(f"/api/team/members/{owner_id}").status_code, 403)
        self.assertEqual(self.roles(owner)[owner_email], "owner")

    def test_admin_can_manage_members_and_admins(self):
        _, owner = self.new_org("Role5")
        admin, _, _ = self.join(owner, "admin", "adm")
        _, member_id, member_email = self.join(owner, "member")
        self.assertEqual(admin.patch(f"/api/team/members/{member_id}/role", json={"role": "admin"}).status_code, 200)
        self.assertEqual(self.roles(owner)[member_email], "admin")
        self.assertEqual(admin.patch(f"/api/team/members/{member_id}/role", json={"role": "member"}).status_code, 200)
        self.assertEqual(admin.delete(f"/api/team/members/{member_id}").status_code, 200)
        self.assertNotIn(member_email, self.roles(owner))

    def test_owner_can_promote_demote_and_remove(self):
        _, owner = self.new_org("Role6")
        _, admin_id, admin_email = self.join(owner, "admin", "adm")
        self.assertEqual(owner.patch(f"/api/team/members/{admin_id}/role", json={"role": "owner"}).status_code, 200)
        self.assertEqual(self.roles(owner)[admin_email], "owner")
        self.assertEqual(owner.patch(f"/api/team/members/{admin_id}/role", json={"role": "member"}).status_code, 200)
        self.assertEqual(owner.delete(f"/api/team/members/{admin_id}").status_code, 200)

    def test_owner_cannot_change_their_own_role(self):
        _, owner = self.new_org("Role7")
        co_owner, _, _ = self.join(owner, "owner", "co")
        owner_email = next(e for e, r in self.roles(owner).items() if r == "owner" and e.startswith("_test_env_team"))
        owner_id = self.user_id(owner, owner_email)
        r = owner.patch(f"/api/team/members/{owner_id}/role", json={"role": "member"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.roles(owner)[owner_email], "owner")

    def test_member_cannot_change_roles_or_remove_anyone(self):
        _, owner = self.new_org("Role8")
        member, member_id, _ = self.join(owner, "member")
        _, other_id, _ = self.join(owner, "member")
        self.assertEqual(member.patch(f"/api/team/members/{other_id}/role", json={"role": "admin"}).status_code, 403)
        self.assertEqual(member.delete(f"/api/team/members/{other_id}").status_code, 403)
        self.assertEqual(member.patch(f"/api/team/members/{member_id}/role", json={"role": "owner"}).status_code, 403)

    def test_unauthenticated_requests_are_rejected(self):
        anon = self.client_factory()
        self.assertEqual(anon.get("/api/team/members").status_code, 401)
        self.assertEqual(anon.patch("/api/team/members/1/role", json={"role": "owner"}).status_code, 401)
        self.assertEqual(anon.delete("/api/team/members/1").status_code, 401)
        self.assertEqual(anon.post("/api/team/invitations", json={"email": "a@b.com", "role": "owner"}).status_code, 401)

    def test_unknown_role_value_is_rejected(self):
        _, owner = self.new_org("Role9")
        _, member_id, _ = self.join(owner, "member")
        self.assertEqual(owner.patch(f"/api/team/members/{member_id}/role", json={"role": "root"}).status_code, 422)


class TestLastOwnerProtection(TeamBase):
    def test_sole_owner_cannot_remove_themselves(self):
        _, owner = self.new_org("Last1")
        owner_email = next(iter(self.roles(owner)))
        owner_id = self.user_id(owner, owner_email)
        r = owner.delete(f"/api/team/members/{owner_id}")
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(self.roles(owner)[owner_email], "owner")

    def test_one_of_two_owners_can_remove_the_other_but_not_leave_none(self):
        _, owner = self.new_org("Last2")
        co_owner, co_id, co_email = self.join(owner, "owner", "co")
        owner_email = next(e for e, r in self.roles(owner).items() if e != co_email)
        owner_id = self.user_id(owner, owner_email)
        self.assertEqual(owner.delete(f"/api/team/members/{co_id}").status_code, 200)
        self.assertEqual(owner.delete(f"/api/team/members/{owner_id}").status_code, 400)
        self.assertEqual(self.roles(owner), {owner_email: "owner"})

    def test_repository_guard_is_atomic_for_demotion(self):
        from backend.app.repositories import memberships_repo
        org_id, owner = self.new_org("Last3")
        _, a_id, a_email = self.join(owner, "owner", "a")
        owner_email = next(e for e in self.roles(owner) if e != a_email)
        owner_id = self.user_id(owner, owner_email)
        self.assertEqual(memberships_repo.update_membership_role_keeping_an_owner(org_id, a_id, "member"), "updated")
        # the remaining owner can no longer be demoted, whoever asks and however many times
        for _ in range(3):
            self.assertEqual(memberships_repo.update_membership_role_keeping_an_owner(org_id, owner_id, "admin"), "last_owner")
        self.assertEqual(memberships_repo.count_owners(org_id), 1)
        self.assertEqual(memberships_repo.update_membership_role_keeping_an_owner(org_id, 987654, "admin"), "not_found")

    def test_repository_guard_is_atomic_for_removal(self):
        from backend.app.repositories import memberships_repo
        org_id, owner = self.new_org("Last4")
        _, a_id, a_email = self.join(owner, "owner", "a")
        owner_email = next(e for e in self.roles(owner) if e != a_email)
        owner_id = self.user_id(owner, owner_email)
        self.assertEqual(memberships_repo.remove_membership_keeping_an_owner(org_id, a_id), "removed")
        self.assertEqual(memberships_repo.remove_membership_keeping_an_owner(org_id, owner_id), "last_owner")
        self.assertEqual(memberships_repo.remove_membership_keeping_an_owner(org_id, 987654), "not_found")
        self.assertEqual(memberships_repo.count_owners(org_id), 1)

    def test_promoting_to_owner_is_never_blocked_by_the_guard(self):
        from backend.app.repositories import memberships_repo
        org_id, owner = self.new_org("Last5")
        _, a_id, _ = self.join(owner, "member", "a")
        self.assertEqual(memberships_repo.update_membership_role_keeping_an_owner(org_id, a_id, "owner"), "updated")
        self.assertEqual(memberships_repo.count_owners(org_id), 2)


class TestTeamCrossOrganization(TeamBase):
    def test_another_organizations_owner_cannot_touch_my_members_or_invitations(self):
        _, owner_a = self.new_org("XOrgA")
        _, owner_b = self.new_org("XOrgB")
        _, member_id, member_email = self.join(owner_a, "member")
        inv = owner_a.post("/api/team/invitations", json={"email": self._next_email("pend"), "role": "member"}).json()

        self.assertEqual(owner_b.patch(f"/api/team/members/{member_id}/role", json={"role": "admin"}).status_code, 404)
        self.assertEqual(owner_b.delete(f"/api/team/members/{member_id}").status_code, 404)
        self.assertEqual(owner_b.delete(f"/api/team/invitations/{inv['id']}").status_code, 404)
        self.assertEqual(self.roles(owner_a)[member_email], "member")
        self.assertTrue(any(i["id"] == inv["id"] for i in owner_a.get("/api/team/invitations").json()))

    def test_member_lists_never_include_another_organization(self):
        _, owner_a = self.new_org("XList A")
        _, owner_b = self.new_org("XList B")
        _, _, member_email = self.join(owner_a, "member")
        self.assertNotIn(member_email, self.roles(owner_b))
        self.assertEqual(owner_b.get("/api/team/invitations").json(), [])


if __name__ == "__main__":
    unittest.main()