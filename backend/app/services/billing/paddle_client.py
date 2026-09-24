"""
The ONLY module in this application allowed to call Paddle's API or import
a Paddle SDK. Every other module -- routes, billing_service.py, product
logic -- must go through the functions here, and must never reach into
Paddle directly. This is the boundary PROJECT_ARCHITECTURE.md requires:

    Internal concepts (everywhere else in the app):
        Organization -> Subscription -> Plan -> Entitlement -> BillingStatus
    Paddle-specific concepts (ONLY here):
        Paddle customer id, Paddle subscription id, Paddle webhook events,
        Paddle's own subscription/price/product ids.

Core product logic (e.g. change_case_rules.check_entitled_for_diagnostic)
reads ONLY the local `subscriptions` table -- it never calls this module
or Paddle directly at request time. This module's job is narrower: turn a
Paddle webhook into a safe, idempotent update to that local table, and
turn a "start checkout" request into a Paddle checkout URL. Nothing here
should be trusted as authoritative until it has been verified.

STATUS: Phase 1 skeleton. No live Paddle API calls are made yet -- the
functions below define the real shape of this boundary and are wired for
verification and idempotency, but the actual HTTP calls to Paddle are
NotImplementedError stubs, following the exact same honesty discipline as
services/billing_service.py's existing Stripe stubs (see that file's
docstring: "explicit stubs so it's obvious what's not built yet, rather
than a fake success response").
"""
import hashlib
import hmac
import json

from backend.app.config.settings import settings
from backend.app.repositories import paddle_webhook_repo


class PaddleWebhookVerificationError(Exception):
    """Raised when a webhook's signature doesn't verify. The caller
    (the webhook route, not written yet in Phase 1) must reject the
    request with 400 and must NOT process the payload -- an unverified
    webhook is untrusted external input, full stop."""


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> None:
    """Verifies a Paddle webhook's signature using PADDLE_WEBHOOK_SECRET.
    Raises PaddleWebhookVerificationError if it doesn't match. This MUST
    be called, and MUST pass, before a single byte of the payload is
    trusted or parsed for meaning -- mirrors the exact discipline already
    documented in billing_service.py's handle_stripe_webhook docstring.

    NOTE: Paddle's actual signature scheme (a timestamped HMAC in the
    `Paddle-Signature` header, ts=...;h1=...) is not yet implemented here
    -- this is a Phase 1 skeleton establishing the boundary and call
    site, not the finished verification logic. Do not wire this to a real
    webhook endpoint until the real scheme is implemented and tested
    against Paddle's own signature examples.
    """
    raise NotImplementedError(
        "Paddle webhook signature verification is not implemented yet. "
        "This is a deliberate Phase 1 stub -- do not bypass this check "
        "or treat an unverified payload as trusted."
    )


def record_and_check_idempotency(paddle_event_id: str, event_type: str, payload: dict) -> bool:
    """Records this webhook event and returns True if it's genuinely new
    (should be processed), or False if we've already seen and recorded
    this exact paddle_event_id before (safe to no-op). Webhook providers,
    including Paddle, can and do redeliver the same event -- this is
    normal, expected behavior to handle, not an edge case."""
    existing = paddle_webhook_repo.get_event_by_paddle_id(paddle_event_id)
    if existing is not None:
        return False
    paddle_webhook_repo.record_received_event(
        paddle_event_id=paddle_event_id,
        event_type=event_type,
        payload_json=json.dumps(payload),
    )
    return True


def create_checkout_url(organization_id: int, plan: str) -> str:
    """Returns a Paddle-hosted checkout URL for this organization to
    subscribe to `plan`. NOT IMPLEMENTED in Phase 1 -- see
    billing_service.create_checkout_session, which this will eventually
    back, for the same honest-stub pattern."""
    raise NotImplementedError(
        "Paddle checkout session creation is not implemented yet. "
        "PADDLE_API_KEY and PADDLE_ENVIRONMENT must be configured in "
        "settings before this can be built out for real."
    )
