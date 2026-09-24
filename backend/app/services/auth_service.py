from backend.app.repositories import organizations_repo, users_repo, memberships_repo, subscriptions_repo, invitations_repo
from backend.app.security.passwords import hash_password, verify_password
from backend.app.schemas.auth_schemas import SignupRequest, LoginRequest
from backend.app.schemas.errors import ValidationError
from backend.app.services.team_rules import check_invitation_usable


def sign_up(body: SignupRequest) -> dict:
    """Creates a user and either (a) a brand new organization they own, or
    (b) joins an existing organization via a valid invite token."""
    if users_repo.get_user_by_email(body.email):
        # Deliberately vague -- do not reveal whether an email exists.
        raise ValidationError("Could not create account with the details provided.", "email")

    if body.invite_token:
        return _sign_up_via_invite(body)
    return _sign_up_new_organization(body)


def _sign_up_new_organization(body: SignupRequest) -> dict:
    if not body.organization_name:
        raise ValidationError("Please enter a company or team name.", field="organization_name")

    org_id = organizations_repo.create_organization(body.organization_name)
    password_hash = hash_password(body.password)
    user_id = users_repo.create_user(body.email, password_hash, body.display_name)
    memberships_repo.create_membership(user_id, org_id, role="owner")
    subscriptions_repo.create_trial_subscription(org_id)

    return {
        "user": users_repo.get_user_public(user_id),
        "organization": organizations_repo.get_organization(org_id),
    }


def _sign_up_via_invite(body: SignupRequest) -> dict:
    invitation = invitations_repo.get_invitation_by_token(body.invite_token)
    if not invitation:
        raise ValidationError("This invite link isn't valid.", field="invite_token")
    check_invitation_usable(invitation)

    if invitation["email"].lower() != body.email.lower().strip():
        # The invite was issued for a specific email -- don't silently join
        # a different email to someone else's org.
        raise ValidationError(
            "This invite was issued for a different email address.", field="email"
        )

    password_hash = hash_password(body.password)
    user_id = users_repo.create_user(body.email, password_hash, body.display_name)
    memberships_repo.create_membership(user_id, invitation["organization_id"], role=invitation["role"])
    invitations_repo.mark_invitation_accepted(invitation["id"])

    return {
        "user": users_repo.get_user_public(user_id),
        "organization": organizations_repo.get_organization(invitation["organization_id"]),
    }


def log_in(body: LoginRequest) -> dict:
    """Verifies credentials and returns the user + their organizations.
    Raises ValidationError with a deliberately generic message on any
    failure (unknown email, wrong password, inactive account)."""
    user = users_repo.get_user_by_email(body.email)

    generic_error = ValidationError("Incorrect email or password.")
    if not user or not user["is_active"]:
        raise generic_error
    if not verify_password(body.password, user["password_hash"]):
        raise generic_error

    memberships = memberships_repo.list_memberships_for_user(user["id"])
    if not memberships:
        raise generic_error

    return {
        "user": users_repo.get_user_public(user["id"]),
        "memberships": memberships,
    }