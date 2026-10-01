"""
Operator (founder-run) actions that are NOT reachable from any HTTP route.

Activation of a directly-sold customer: moves an organization off the
trial plan so the existing entitlement rule
(change_case_rules.check_entitled_for_diagnostic) lets it rank. This uses
the existing subscriptions table and that unchanged rule -- it adds no
new entitlement concept and does not touch Paddle.

Safety properties:
- the organization must be identified explicitly, by exactly one of id or
  exact name; a name that matches zero or several organizations is refused
- only paid plans ('pilot', 'enterprise') can be activated
- idempotent: re-running with the same target changes nothing
- every change is audit-logged (no user id: it is an operator action)
"""
from backend.app.repositories import organizations_repo, subscriptions_repo
from backend.app.schemas.errors import ValidationError
from backend.app.services import audit_service

ACTIVATABLE_PLANS = ("pilot", "enterprise")
AUDIT_ACTION = "operator_activate_subscription"


def _resolve_organization(org_id, org_name) -> dict:
    if (org_id is None) == (org_name is None):
        raise ValidationError("Identify the organization with exactly one of: organization id, organization name.")
    if org_id is not None:
        org = organizations_repo.get_organization(org_id)
        if not org:
            raise ValidationError(f"No organization with id {org_id}. Nothing was changed.")
        return org
    if not str(org_name).strip():
        raise ValidationError("Organization name is empty. Nothing was changed.")
    matches = organizations_repo.find_organizations_by_exact_name(org_name)
    if not matches:
        raise ValidationError(f"No organization named {org_name!r}. Nothing was changed.")
    if len(matches) > 1:
        ids = ", ".join(str(m["id"]) for m in matches)
        raise ValidationError(
            f"{len(matches)} organizations are named {org_name!r} (ids: {ids}). "
            "Re-run with the organization id. Nothing was changed."
        )
    return matches[0]


def activate_subscription(org_id=None, org_name=None, plan: str = "pilot", apply: bool = True) -> dict:
    """Returns a result dict describing the organization and its plan/status
    before and after. With apply=False nothing is written (dry run)."""
    if plan not in ACTIVATABLE_PLANS:
        raise ValidationError(f"Plan must be one of {', '.join(ACTIVATABLE_PLANS)}. Nothing was changed.")
    org = _resolve_organization(org_id, org_name)
    sub = subscriptions_repo.get_subscription(org["id"])
    if not sub:
        raise ValidationError(
            f"Organization {org['id']} ({org['name']!r}) has no subscription record. Nothing was changed."
        )
    already = sub["plan"] == plan and sub["status"] == "active"
    result = {
        "organization_id": org["id"],
        "organization_name": org["name"],
        "previous_plan": sub["plan"],
        "previous_status": sub["status"],
        "plan": plan if (apply or already) else sub["plan"],
        "status": "active" if (apply or already) else sub["status"],
        "changed": False,
        "applied": apply,
    }
    if already:
        return result
    if not apply:
        result["would_change_to"] = {"plan": plan, "status": "active"}
        return result
    subscriptions_repo.set_plan_and_status(org["id"], plan, "active")
    audit_service.log(
        org["id"], None, AUDIT_ACTION,
        detail=f"plan {sub['plan']}/{sub['status']} -> {plan}/active",
    )
    result["changed"] = True
    return result