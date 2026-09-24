"""
Billing. This is DELIBERATELY a stub -- there is no real Stripe integration
in this codebase. Do not present this to a customer as "billing is live."

What's real: the `subscriptions` table and `get_billing_summary()`, which
reflects actual stored plan/status (defaulting every new organization to a
'trial' subscription, see services/auth_service.py -> subscriptions_repo).

What's NOT implemented (and must not be faked):
  - Stripe Checkout session creation
  - Stripe webhook handling (subscription created/updated/canceled,
    invoice payment failed, etc.)
  - Actual charging of a payment method

When real Stripe integration is added, it belongs entirely server-side:
  - The Stripe SECRET key lives only in backend environment variables,
    never in frontend code or NEXT_PUBLIC_* variables.
  - Webhook signature verification (STRIPE_WEBHOOK_SECRET) must happen
    before trusting any webhook payload.
  - The frontend only ever receives a Checkout Session URL to redirect to,
    or publishable-key-based Stripe.js calls -- never secret keys.
"""
from backend.app.repositories import subscriptions_repo


def get_billing_summary(organization_id: int) -> dict:
    sub = subscriptions_repo.get_subscription(organization_id)
    if not sub:
        return {"plan": "none", "status": "inactive", "stripe_configured": False}
    return {
        "plan": sub["plan"],
        "status": sub["status"],
        "current_period_end": sub["current_period_end"],
        "stripe_configured": bool(sub["stripe_customer_id"]),
    }


def create_checkout_session(organization_id: int, plan: str) -> dict:
    raise NotImplementedError(
        "Stripe Checkout is not implemented in this MVP. Wire this to "
        "stripe.checkout.Session.create(...) server-side once a Stripe "
        "account and STRIPE_SECRET_KEY are configured -- see module docstring."
    )


def handle_stripe_webhook(payload: bytes, signature_header: str) -> None:
    raise NotImplementedError(
        "Stripe webhook handling is not implemented in this MVP. When added, "
        "verify `signature_header` against STRIPE_WEBHOOK_SECRET BEFORE "
        "trusting `payload` -- see module docstring."
    )
