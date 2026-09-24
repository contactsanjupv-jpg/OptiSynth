from sqlalchemy import text

from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def get_event_by_paddle_id(paddle_event_id: str):
    """Look up a previously-recorded webhook event by Paddle's own event
    id. Used for the idempotency check BEFORE processing -- see
    services/billing/paddle_client.py's module docstring for why this
    must always be checked first."""
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM paddle_webhook_events WHERE paddle_event_id = :event_id"),
            {"event_id": paddle_event_id},
        ).mappings().first()
        return dict(row) if row else None


def record_received_event(paddle_event_id: str, event_type: str, payload_json: str) -> dict:
    """Records a webhook event as 'received', BEFORE any internal
    subscription state is touched. Relies on paddle_event_id's UNIQUE
    constraint (migrations/versions/83dc09944945_*.py) as the real
    idempotency guarantee -- a duplicate insert raises IntegrityError,
    which the caller should treat as 'already seen, safe to no-op',
    not as an error to surface."""
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO paddle_webhook_events
                     (paddle_event_id, event_type, payload_json, processing_status, received_at)
                     VALUES (:event_id, :event_type, :payload, 'received', :received_at)
                     RETURNING id"""),
            {
                "event_id": paddle_event_id,
                "event_type": event_type,
                "payload": payload_json,
                "received_at": now_iso(),
            },
        ).mappings().first()
        return {"id": row["id"]}


def mark_event_processed(paddle_event_id: str) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE paddle_webhook_events SET processing_status = 'processed', processed_at = :processed_at
                     WHERE paddle_event_id = :event_id AND processing_status = 'received'"""),
            {"event_id": paddle_event_id, "processed_at": now_iso()},
        )
        return result.rowcount > 0


def mark_event_failed(paddle_event_id: str) -> bool:
    with db_transaction() as conn:
        result = conn.execute(
            text("""UPDATE paddle_webhook_events SET processing_status = 'failed', processed_at = :processed_at
                     WHERE paddle_event_id = :event_id"""),
            {"event_id": paddle_event_id, "processed_at": now_iso()},
        )
        return result.rowcount > 0
