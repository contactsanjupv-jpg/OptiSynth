from sqlalchemy import text
from backend.app.config.database import db_transaction, db_connection
from backend.app.repositories._time import now_iso


def create_trial_subscription(organization_id: int) -> int:
    with db_transaction() as conn:
        row = conn.execute(
            text("""INSERT INTO subscriptions (organization_id, plan, status, created_at, updated_at)
                     VALUES (:org_id, 'trial', 'active', :created_at, :updated_at) RETURNING id"""),
            {"org_id": organization_id, "created_at": now_iso(), "updated_at": now_iso()},
        ).mappings().first()
        return row["id"]


def get_subscription(organization_id: int):
    with db_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM subscriptions WHERE organization_id = :org_id"),
            {"org_id": organization_id},
        ).mappings().first()
        return dict(row) if row else None
