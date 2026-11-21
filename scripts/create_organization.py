"""Creates an organization and its first owner. This is how a pilot customer starts when public sign-up is off.

    python3 scripts/create_organization.py --organization "Acme Materials" --email owner@acme.com --name "Ada Owner"

The password is read from the prompt, or from the OPTISYNTH_OWNER_PASSWORD variable for scripted use. Run it with the same
environment (DATABASE_URL, SECRET_KEY, PASSWORD_PEPPER) as the application, after `alembic upgrade head`.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.app.schemas.auth_schemas import SignupRequest  # noqa: E402
from backend.app.schemas.errors import ValidationError  # noqa: E402
from backend.app.services import audit_service, auth_service  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an organization and its first owner")
    parser.add_argument("--organization", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default=None)
    args = parser.parse_args()
    password = os.environ.get("OPTISYNTH_OWNER_PASSWORD") or getpass.getpass("Owner password: ")
    try:
        body = SignupRequest(email=args.email, password=password, organization_name=args.organization, display_name=args.name)
        result = auth_service.sign_up(body)
    except ValidationError as err:
        print(f"Could not create the organization: {err.message}", file=sys.stderr)
        return 1
    except ValueError as err:
        print(f"Could not create the organization: {err}", file=sys.stderr)
        return 1
    audit_service.log(result["organization"]["id"], result["user"]["id"], "auth.signup", detail="created by operator")
    print(f"Created organization {result['organization']['id']} with owner {result['user']['email']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
