# OptiSynth — Project Architecture

This document is the source of truth for architectural decisions. Any
session (human or Claude) making a non-trivial change should read this
first, then `CLAUDE.md` for session workflow.

## Domain model

Two parallel product domains coexist deliberately — the original
optimization domain is not deleted:

```
organizations
  ← memberships, invitations           (shared identity/tenancy layer)
  ← projects → datasets, experiments, recommendations, model_runs, reports
      (ORIGINAL domain: general Bayesian optimization toward a target)
  ← change_cases → qualification_datasets, candidate_substitutes,
      predictions, recommended_experiments, qualification_outcomes, reports
      (NEW domain, Phase 1: regulatory-driven substitute qualification —
      the primary commercial product going forward)
  ← subscriptions                       (billing state; Paddle-backed)
  ← paddle_webhook_events               (webhook idempotency/audit log)
  ← audit_logs
```

## Non-negotiable rules

1. `organization_id` is NEVER trusted from client input (body, query,
   path). It comes ONLY from `api/dependencies.py::get_current_actor`,
   derived from the verified session cookie, re-checked against active
   membership on every request. Every route uses `Depends(get_current_actor)`
   and passes `actor.organization_id` explicitly to every service/
   repository call. Verified: `test_api.py::test_tenant_isolation_cannot_see_other_orgs_project`.
2. `engine/` has zero knowledge of FastAPI, SQLAlchemy, or HTTP. The web
   layer talks to it ONLY through `engine/facade.py`'s public functions
   (`recommend_next`, `run_backtest`, `compute_model_metrics`).
3. Business rules with real safety/security implications live in small,
   pure, dependency-free modules (`services/*_rules.py` — zero fastapi/
   sqlalchemy/pydantic imports), unit-testable without those packages
   installed. Established by `project_rules.py`, `team_rules.py`;
   extended in Phase 1 by `change_case_rules.py`. Follow this pattern for
   new business logic rather than inventing a new convention.
4. `qualification_outcomes` rows are append-only. Never UPDATE a recorded
   outcome — enforced at the application layer by
   `change_case_rules.check_outcome_not_already_recorded`. This table is
   the company's core proprietary asset; its audit integrity matters more
   than convenience. A correction is a NEW row referencing the original,
   never an overwrite.
5. Every prediction is stamped with the dataset version and model version
   that produced it (`predictions.dataset_version_id`,
   `predictions.model_version`) — we must always be able to answer
   "exactly which data and model produced this recommendation."
6. Schema changes go through Alembic migrations under
   `backend/migrations/versions/` — never hand-edit `schema.sql` and call
   it done. `schema.sql` is kept as a readable snapshot of the ORIGINAL
   (pre-Alembic) schema for reference; it is allowed to drift from the
   true current schema, since Alembic's migration history is now the
   actual source of truth. Every migration has a real `downgrade()`.
   The baseline migration (`bd1d666d39bf`) deliberately REFUSES to
   downgrade past itself — this is intentional, not a bug: it prevents an
   accidental full-application teardown via a careless `alembic downgrade
   base`.
7. Paddle is reached ONLY through `services/billing/paddle_client.py`.
   Core product logic (entitlement checks, e.g.
   `change_case_rules.check_entitled_for_diagnostic`) reads ONLY the
   local `subscriptions` table — never calls Paddle live at request time.
   Webhooks are untrusted input: verify signature
   (`paddle_client.verify_webhook_signature` — NOT YET IMPLEMENTED, see
   Known limitations below), check idempotency via
   `paddle_webhook_repo`/`paddle_webhook_events` (proven: duplicate event
   IDs are correctly no-op'd) BEFORE processing, before touching internal
   subscription state.
8. File uploads: never trust the client filename for storage, generate a
   random server-side name, org-scoped storage path derived only from the
   server-verified `organization_id`, stdlib `csv` parsing only. Applies
   to both `datasets` (original domain) and `qualification_datasets`
   (new domain) identically.
9. No secret, ever, in frontend code or `NEXT_PUBLIC_*` variables.
10. The application refuses to start against a database that isn't at
    the latest migration — `config/database.py::verify_migrations_at_head()`,
    called from `main.py`'s lifespan. Fails loudly (`MigrationsNotAtHeadError`)
    rather than silently running against a mismatched schema. Verified
    both ways: real startup succeeds at head, real startup refuses one
    migration behind.

## Migration workflow (Alembic)

```
cd backend
alembic upgrade head          # apply all pending migrations
alembic current                # show the DB's current revision
alembic downgrade -1           # roll back one migration
alembic revision -m "message"  # scaffold a new migration -- fill in
                                # upgrade()/downgrade() by hand; this repo
                                # writes migrations explicitly (no ORM
                                # models to autogenerate from -- SQLAlchemy
                                # Core only, by deliberate choice)
```

For a database that already has the pre-Alembic schema (i.e. anyone's
existing local `data/rdopt.db` from before Phase 1): run
`alembic stamp bd1d666d39bf` once, NOT `alembic upgrade head` — stamping
tells Alembic "this schema already exists, start tracking from here"
without re-running any SQL or touching existing rows.

`init_db()` in `database.py` still exists and still directly executes
`schema.sql` — kept ONLY because `backend/tests/test_api.py` depends on
it for fast, isolated test-database creation. It is NOT how the real
running application initializes its schema anymore; do not call it from
application startup code.

## Known, accepted limitations (accurate as of Phase 1)

- `httpx` is missing from `backend/requirements.txt`, which makes
  `test_api.py`'s 11 tests fail at setup (FastAPI's `TestClient` requires
  it). This is a pre-existing gap, confirmed by direct testing, not
  introduced by Phase 1 — flagged for a follow-up fix, deliberately not
  bundled into Phase 1's scope.
- `paddle_client.verify_webhook_signature` and `create_checkout_url` are
  intentional `NotImplementedError` stubs. Do not wire a real webhook
  endpoint or checkout flow to them until Paddle's real signature scheme
  and API are implemented and tested against Paddle's own examples.
- File upload size is checked after the request body is fully buffered
  into memory (no reverse-proxy-level cap yet). Acceptable at current
  scale; should be closed before accepting uploads from untrusted
  networks at volume.
- Test coverage on the original optimization domain is real but thin
  (~625 lines total, pre-Phase-1). New domain code should not inherit
  this gap — `change_case_rules.py` shipped with 19 tests alongside it,
  not after.
