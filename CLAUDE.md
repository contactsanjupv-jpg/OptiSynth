# Instructions for any Claude session working on this repository

Read `PROJECT_ARCHITECTURE.md` in full before making any change.

## Before implementing anything

1. Inspect the actual current code relevant to the task — clone/pull the
   real repository and read the real files. Do not assume this file, or
   any prior session's summary, is fully up to date. If a docstring or
   comment claims something is untested and you have reason to believe
   otherwise (or vice versa), verify by actually running it and correct
   the docstring — don't trust either blindly. (Several such stale claims
   were found and fixed during Phase 1; expect more may exist elsewhere.)
2. Plan the change: which files, which migration (if the schema
   changes), which tests. State the plan before editing code, and wait
   for approval on anything architecturally non-trivial.
3. Identify risks explicitly, especially: can Tenant A ever reach Tenant
   B's data through this change? Does this touch `qualification_outcomes`
   in a way that isn't append-only? Does this call Paddle from anywhere
   other than `services/billing/paddle_client.py`?

## While implementing

4. Prefer small, focused changes over large rewrites. Extend the
   `*_rules.py` pure-module pattern for new business logic.
5. Any schema change is a new Alembic migration under
   `backend/migrations/versions/`, written by hand (upgrade AND
   downgrade), never a hand-edit to `schema.sql`. Test the migration for
   real — apply it, verify the resulting schema, then test the downgrade
   too, including what happens if upgrade is run again afterward (a real
   bug was caught this way in Phase 1: a downgrade that intentionally
   left columns in place broke a subsequent re-upgrade until the column-add
   was made idempotent).
6. Write or update tests alongside the change, not after — security-
   boundary tests (tenant isolation, entitlement checks) are not
   optional.

## Before reporting back

7. Actually run the relevant tests. Don't report a test as passing
   without having executed it in this session. If a test suite can't be
   run in the current environment (missing dependency, no network), say
   so explicitly rather than assuming it would pass.
8. Report exactly what changed, what was actually run and verified, and
   what was NOT verified (e.g., "not tested against a real Postgres
   instance — only SQLite in this session"). This project's existing
   culture (see `README.md`, this file's own history) treats explicit
   honesty about what's proven vs. assumed as a hard requirement, not a
   nicety. Never claim something is "tested and working" without having
   executed it.
9. If you find a pre-existing bug or stale claim unrelated to your
   current task (Phase 1 found one: `httpx` missing from
   `requirements.txt`, contradicting earlier project history that said it
   was fixed), flag it clearly rather than silently fixing it — scope
   creep is a real risk on a project this size, and the person you're
   working with has explicitly asked not to expand scope without
   approval.
