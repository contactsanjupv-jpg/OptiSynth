"""
OPERATOR TOOL -- activates a directly-sold customer organization so it can run
the paid diagnostic (ranking). Not reachable from the web app.

Identify the organization by EXACTLY ONE of --org-id / --org-name.
Default is a DRY RUN; nothing is written unless --apply is given.
Idempotent: running it again on an already-activated organization changes nothing.

Usage:
    python3 scripts/activate_subscription.py --org-id 12 --plan pilot            # dry run
    python3 scripts/activate_subscription.py --org-id 12 --plan pilot --apply
    python3 scripts/activate_subscription.py --org-name "Acme Coatings" --plan enterprise --apply

Uses the same DATABASE_URL / SECRET_KEY / PASSWORD_PEPPER environment as the app.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.app.config import database
from backend.app.schemas.errors import ValidationError
from backend.app.services import operator_service


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Activate a customer organization's paid plan (operator only).")
    parser.add_argument("--org-id", type=int, default=None)
    parser.add_argument("--org-name", type=str, default=None, help="exact organization name")
    parser.add_argument("--plan", choices=operator_service.ACTIVATABLE_PLANS, default="pilot")
    parser.add_argument("--apply", action="store_true", help="actually write the change (default: dry run)")
    args = parser.parse_args(argv)

    print(f"Database: {database.engine.url.render_as_string(hide_password=True)}")
    try:
        result = operator_service.activate_subscription(
            org_id=args.org_id, org_name=args.org_name, plan=args.plan, apply=args.apply,
        )
    except ValidationError as e:
        print(f"REFUSED: {e}")
        return 1

    print(f"Organization: {result['organization_name']} (id {result['organization_id']})")
    print(f"Before:       plan={result['previous_plan']} status={result['previous_status']}")
    print(f"After:        plan={result['plan']} status={result['status']}")
    if not result["applied"]:
        if "would_change_to" in result:
            print("DRY RUN -- nothing written. Re-run with --apply to make this change.")
        else:
            print("DRY RUN -- already activated; nothing to change.")
    elif result["changed"]:
        print("APPLIED.")
    else:
        print("No change -- already activated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())