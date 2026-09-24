# OptiSynth / rdopt — R&D Optimization Platform

Find better candidates with fewer experiments. A narrow, sellable
Bayesian-optimization product for formulation and process-parameter R&D
teams — not a generic "AI scientist."

---

## 1. Architecture

```
                    ┌─────────────────────┐
                    │   frontend/ (Next.js)│
                    │   TypeScript, App     │
                    │   Router, cookie auth │
                    └──────────┬───────────┘
                               │ fetch(), credentials: "include"
                               │ httpOnly signed session cookie
                               ▼
                    ┌─────────────────────┐
                    │   backend/ (FastAPI)  │
                    │  api/routes           │
                    │  api/dependencies      │◄── tenant isolation
                    │  services              │    enforced HERE
                    │  repositories           │
                    │  security / middleware  │
                    └──────────┬───────────┘
                               │ engine.facade (ONLY entry point)
                               ▼
                    ┌─────────────────────┐
                    │   engine/ (pure Python)│
                    │  models / preprocessing│
                    │  acquisition / constraints
                    │  optimization / evaluation
                    │  benchmarks             │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  SQLite (dev) or       │
                    │  PostgreSQL (prod)      │
                    │  via SQLAlchemy Core    │
                    └─────────────────────┘
```

**Strict separation of concerns**, matching the required layout:

```
rdopt-complete/
├── backend/
│   └── app/
│       ├── main.py            # FastAPI app assembly ONLY — no business logic
│       ├── api/
│       │   ├── routes/        # HTTP layer — one file per resource
│       │   └── dependencies.py# get_current_actor() — tenant isolation lives HERE
│       ├── auth/               # (reserved — auth logic currently lives in security/ + services/auth_service.py)
│       ├── models/
│       │   ├── schema.sql      # portable multi-tenant SQL schema
│       │   └── entities.py     # typed dataclass documentation of rows
│       ├── schemas/            # Pydantic request/response validation
│       ├── services/           # business logic — the ONLY layer that calls engine/
│       ├── repositories/       # one file per DB entity, SQLAlchemy Core, org-scoped
│       ├── middleware/         # error handling, security headers
│       ├── security/           # passwords, sessions, legacy API keys
│       └── config/             # settings.py, database.py — the only files reading os.environ
│   ├── requirements.txt
│   └── tests/
├── engine/                     # ZERO knowledge of HTTP/DB/auth — pure numpy in, dicts out
│   ├── models/                 # GP surrogate construction
│   ├── preprocessing/          # feature normalization
│   ├── acquisition/            # Expected Improvement
│   ├── constraints/             # feasibility probability
│   ├── optimization/            # candidate generation, the recommend() loop
│   ├── evaluation/              # backtest, cross-validated model metrics
│   ├── benchmarks/              # synthetic reproducible benchmark (sales demos)
│   ├── facade.py                # the ONLY module backend/services may import from
│   └── tests/
├── frontend/                   # Next.js 14, App Router, TypeScript
│   ├── app/                    # one folder per route, no giant page.tsx
│   ├── components/             # layout / dashboard / projects / experiments / charts / tables / ui
│   ├── lib/
│   │   ├── api/                # typed client — the ONLY code that calls fetch()
│   │   ├── auth/                # session-cache + auth guard hook
│   │   └── utils/                # formatting helpers
│   └── types/
├── data/                        # SQLite DB file (dev) + uploaded CSVs (gitignored)
├── reports/generated/           # generated .docx reports (gitignored)
├── .env.example
├── .gitignore
└── README.md (this file)
```

---

## 2. Why SQLAlchemy Core, not the full ORM

The repository layer uses SQLAlchemy **Core** (`sqlalchemy.text()` with
named bind parameters) rather than the declarative ORM. This was a
deliberate choice to keep the already-designed, already-reviewed SQL schema
(`backend/app/models/schema.sql`) as the single source of truth, rather
than duplicating it as ORM model classes. The tradeoff: you don't get
`session.query(Project).filter(...)` — you get explicit, readable SQL. If
your team prefers the full ORM, `models/entities.py`'s dataclasses are a
reasonable starting point for declarative model classes; the migration
touches only `repositories/`, nothing above it.

Every INSERT uses `RETURNING id` (supported by SQLite 3.35+ and
PostgreSQL) instead of driver-specific `lastrowid`, so the exact same SQL
runs unchanged against both databases — this is what "PostgreSQL-ready
architecture" means concretely here: not a promise, a specific, auditable
mechanism (see any file in `backend/app/repositories/`).

---

## 3. Sandbox limitations — please read before judging test coverage

This project was built in a sandboxed environment with **no access to the
npm or PyPI package registries** beyond what was already cached. Concretely:
`fastapi`, `sqlalchemy`, `pydantic`, `uvicorn`, `psycopg2`, `pytest`, and
Next.js/React itself could not be installed there, and no PostgreSQL server
was available. This is stated plainly, not glossed over — see the
verification report at the end of this README for exactly what was and
wasn't executed as a result.

What **was** available and used for real, executed verification:
Python's standard library (`unittest`, `hashlib`, `itsdangerous` was
pre-cached), `numpy`/`scipy`/`scikit-learn`/`pandas`, and `python-docx`.

**Every backend `.py` file was still syntax-validated** with
`python3 -m py_compile` — this catches real syntax errors (typos, bad
indentation, mismatched brackets) even without the imports being
resolvable, because `py_compile` only parses to an AST; it does not
execute imports. It does **not** catch type errors, wrong function
signatures against a real library's API, or logic bugs that only surface
at runtime — those require the commands in Section 7 run on your machine.

---

## 4. Prerequisites

- Python 3.11+
- Node.js 18.18+ and npm
- PostgreSQL 14+ (production only — SQLite works out of the box for local dev)

---

## 5. Installation

```bash
git clone <this repo>
cd rdopt-complete
cp .env.example .env
```

Generate two secrets and paste them into `.env`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"   # -> SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"   # -> PASSWORD_PEPPER (use a DIFFERENT value)
```

### Backend

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

### Frontend

```bash
cd frontend
cp .env.local.example .env.local
npm install
```

---

## 6. Database setup

**Local dev (SQLite, default, zero setup):** nothing to do — the app
creates `data/rdopt.db` and all tables automatically on first startup
(`init_db()` in `backend/app/config/database.py`).

**Production (PostgreSQL):**

```bash
createdb rdopt
```

Set in `.env`:
```
DATABASE_URL=postgresql+psycopg2://rdopt_user:password@localhost:5432/rdopt
```

`init_db()` will create the schema from `models/schema.sql` on startup —
fine for a first deploy, but for ongoing schema changes in production, set
up [Alembic](https://alembic.sqlalchemy.org/) migrations instead (not
included in this MVP — `init_db()` has no versioning or rollback).

---

## 7. Exact commands to run on your machine

**These have NOT been executed in the sandbox that built this project —
run them for real before deploying, and please report back anything that
fails so it can be fixed.**

### Backend

```bash
source .venv/bin/activate
python3 -m unittest backend.tests.test_api -v         # FastAPI TestClient e2e suite (11 tests)
uvicorn backend.app.main:app --reload --port 5050      # start the API
# Visit http://127.0.0.1:5050/docs for interactive API docs (dev only)
```

### Frontend

```bash
cd frontend
npm install
npm run lint
npx tsc --noEmit
npm run build
npm run dev      # http://localhost:3000
```

If `npx tsc --noEmit` reports **"No inputs were found in config file"** —
this should NOT happen with the files in this ZIP (34+ real `.ts`/`.tsx`
files exist under `app/`, `components/`, `lib/`, `types/` matching
`tsconfig.json`'s `include` globs). If you still see it, you're most
likely running the command from the wrong directory — it must be run from
inside `frontend/`, not the repo root.

---

## 8. Running all tests that actually run in this sandbox

```bash
python3 -m unittest discover -s engine/tests -v          # 16 tests
python3 -m unittest backend.tests.test_security -v       # 9 tests
python3 -m unittest backend.tests.test_sessions -v       # 5 tests
python3 -m unittest backend.tests.test_report_builder -v # 2 tests
```

---

## 9. What changed between the Flask and FastAPI versions

Earlier in this project's development, a complete backend was built and
**runtime-tested** on Flask (11/11 end-to-end tests passing, including a
real tenant-isolation test) because Flask was the only web framework
installed in the sandbox. Per explicit direction, the final architecture
was then ported to FastAPI, since that's the intended production stack —
Flask is **not** part of this deliverable and is not documented as the
backend anywhere in this codebase.

What ported over **unchanged** (these files have zero web-framework
dependency either way):
- `engine/` — the entire optimization engine
- `security/passwords.py`, `security/api_keys.py` — stdlib crypto
- `models/schema.sql`, `models/entities.py`
- `services/dataset_service.py`, `report_builder.py`, `dashboard_service.py`, `billing_service.py`, `audit_service.py`

What was **rewritten** for FastAPI (and is therefore unexecuted in the
sandbox, syntax-validated only):
- `repositories/*.py` — sqlite3 raw connections → SQLAlchemy Core
- `schemas/*.py` — manual validation functions → Pydantic `BaseModel`s
- `security/sessions.py` — Flask's built-in `session` → `itsdangerous`-signed cookie set manually via FastAPI `Response`
- `api/dependencies.py` — Flask `@require_auth` decorator → FastAPI `Depends()`
- `api/routes/*.py` — Flask Blueprints → FastAPI `APIRouter`s
- `main.py` — Flask app factory → FastAPI app factory

The logic inside each function (tenant-isolation checks, validation rules,
error messages) is the same logic that was proven under Flask — the port
changed the framework glue around it, not the security-relevant decisions
themselves. That's why this is described as low-risk despite being
unexecuted, not zero-risk — **please still run Section 7 for real.**

---

## 10. Security considerations

- **Passwords**: PBKDF2-HMAC-SHA256, 600,000 iterations (OWASP 2023
  guidance), random 16-byte salt per user, server-side pepper from
  environment. Never stored or logged in plaintext.
- **Sessions**: signed (HMAC via `itsdangerous`), httpOnly, `SameSite=Lax`
  cookies. Not a bare token — tampering invalidates the signature. The
  payload is still re-verified against the database on every request
  (`api/dependencies.py`), so a deactivated user loses access immediately,
  not at next login.
- **Tenant isolation**: every table that stores customer data carries
  `organization_id`; every repository function requires it as an explicit
  parameter and filters by it. `organization_id` is derived ONLY from the
  verified session in `api/dependencies.py` — no route reads it from a
  request body, query string, or URL segment.
- **File uploads**: CSV only, byte-size and row-count capped, parsed with
  Python's stdlib `csv` module (never `eval`/exec), every cell coerced
  through `float()` inside try/except. Stored filenames are server-
  generated random hex, never the client-supplied filename — this closes
  the path-traversal class of bug entirely, since the "path" is never
  client-influenced. See `services/dataset_service.py`.
- **Errors**: centralized in `middleware/error_handling.py`. Only
  `ValidationError.message` (written by application code specifically to
  be customer-safe) and Pydantic shape errors (generic message) ever reach
  a response body. All other exceptions are logged server-side only.
- **CORS**: a specific origin (`CORS_ALLOWED_ORIGIN`), never a wildcard —
  required because `allow_credentials=True` (needed for the cookie) is
  rejected by browsers when combined with a wildcard origin anyway.
- **Secrets**: `SECRET_KEY`, `PASSWORD_PEPPER`, `API_KEY_PEPPER`,
  `DATABASE_URL` are read only in `config/settings.py`, only from
  environment variables, with no insecure fallback (`_require()` raises on
  startup if missing). `.env` is gitignored; only `.env.example` (all
  placeholders) is committed.
- **Billing**: `services/billing_service.py` is an honest stub — real
  subscription *state* (trial/active/canceled) is stored and returned, but
  there is NO live Stripe integration. `create_checkout_session` and
  `handle_stripe_webhook` both raise `NotImplementedError` with a clear
  docstring rather than faking success.

### Known limitations (be aware of these before a real launch)

- No rate limiting on `/api/auth/login` or `/api/auth/signup` — add this
  (e.g. via a reverse proxy or `slowapi`) before exposing publicly, to
  slow down credential-stuffing/enumeration attempts.
- FastAPI/Starlette has no equivalent to Flask's `MAX_CONTENT_LENGTH` that
  rejects an oversized request body before it's buffered into memory — the
  byte-size check in `dataset_service.py` happens *after* the upload is
  read. For internet-facing deployments, enforce a body-size limit at the
  reverse proxy (nginx `client_max_body_size`, or your load balancer)
  in front of the app as well.
- `init_db()` is a create-if-missing convenience, not a migration tool —
  adopt Alembic before making schema changes against a production database
  with real data in it.
- Team/member management has a real data model (`memberships` table) but
  no routes or UI wired up yet — see `app/team/page.tsx`'s own docstring.

---

## 11. Where the optimization engine lives, and how it's called

`engine/` (project root, sibling to `backend/`) has zero imports from
`backend`, `fastapi`, `flask`, or any database library — it takes numpy
arrays and plain dicts in, returns plain dicts out. `engine/facade.py` is
the only module `backend/app/services/optimization_service.py` imports
from; nothing else in `backend/` reaches into `engine/optimization/`,
`engine/models/`, etc. directly. This means the engine can be developed,
tested, and versioned independently of the web layer — see
`engine/tests/test_optimization.py` (16 tests, all passing, no web
framework involved at all).

---

## 12. How the frontend talks to the backend

```
component (e.g. app/dashboard/page.tsx)
    ↓ calls a typed function
lib/api/*.ts  (e.g. getDashboardSummary())
    ↓ goes through
lib/api/http.ts#apiRequest()   <- the ONLY function that calls fetch()
    ↓  credentials: "include"  (sends the httpOnly session cookie)
FastAPI route (backend/app/api/routes/*.py)
    ↓ Depends(get_current_actor)   <- tenant isolation enforced here
services/*.py
    ↓
repositories/*.py  (SQLAlchemy Core, org-scoped)
    ↓
SQLite / PostgreSQL
```

No component calls `fetch()` directly — grep for it, there are none.
Auth state is never stored in `localStorage`; the httpOnly cookie is
invisible to JavaScript by design (mitigates XSS-based session theft).
`lib/auth/session.ts` caches only non-secret display info (email, org id)
in `sessionStorage` for UI purposes — clearing it does not log anyone out;
only `POST /api/auth/logout` does that.

---

## 13. Verification report

### PASS — actually executed in this sandbox, real output shown during the build

| Suite | Tests | What it proves |
|---|---|---|
| `engine/tests/test_optimization.py` | 16 | GP surrogate, EI acquisition, constraint feasibility, candidate recommendation, backtest, cross-validated model metrics — all real math, real assertions |
| `backend/tests/test_security.py` | 9 | Password hashing (PBKDF2) and legacy API-key hashing — correct verify, wrong password/key rejected, malformed hash fails closed, no plaintext leakage |
| `backend/tests/test_sessions.py` | 5 | Session token signing via itsdangerous — round-trip, tamper detection, garbage/empty rejection |
| `backend/tests/test_report_builder.py` | 2 | Real .docx generation and content verification via python-docx |
| **Total** | **32 real, executed, passing tests** | |

Additionally, **every backend `.py` file (54 files) and every engine
`.py` file (21 files) was syntax-validated** with `python3 -m py_compile`
— zero syntax errors.

An earlier, functionally-equivalent **Flask** version of the full backend
(routes, tenant isolation, dataset upload, recommend, backtest, report
generation) was also runtime-tested with **11/11 passing end-to-end
tests**, including a real tenant-isolation test where a second
organization genuinely received a 404 for the first organization's
project. That version is not part of this deliverable (see Section 9) but
is strong evidence the *logic* being ported is sound.

### NOT TESTABLE IN SANDBOX — explain why

| What | Why |
|---|---|
| `backend/tests/test_api.py` (11 FastAPI e2e tests) | `fastapi`/`httpx` not installed; no network to the package index in this sandbox |
| Every FastAPI route file, `main.py`, `dependencies.py`, `middleware/*.py` | Depend on `fastapi`/`starlette`, not installed |
| Every `repositories/*.py` file | Depend on `sqlalchemy`, not installed |
| Every `schemas/*.py` file | Depend on `pydantic`, not installed |
| PostgreSQL-specific behavior | No PostgreSQL server available in the sandbox |
| Entire `frontend/` — build, lint, type-check | `npm install` cannot reach the npm registry (403) in this sandbox; no `node_modules`, so no meaningful `next build`/`tsc`/`eslint` run is possible |

All of the above were **syntax-validated** (`py_compile` for Python; every
`@/` import in the frontend was checked to resolve to a real file) but not
executed.

### MANUAL TEST REQUIRED ON YOUR MAC

Run every command in Section 7, in order. In particular:

```bash
pip install -r backend/requirements.txt
python3 -m unittest backend.tests.test_api -v
uvicorn backend.app.main:app --reload --port 5050
```
```bash
cd frontend && npm install && npm run lint && npx tsc --noEmit && npm run build
```

Please report back anything that fails — the most likely failure classes,
given how this was built, are: a Pydantic v2 API detail that differs
slightly from what's written (e.g. `Field` constraint syntax), a
SQLAlchemy 2.0 Core usage detail, or a Next.js App Router convention that
needs adjusting once real type-checking runs against it.

---

## 14. Production deployment (outline)

1. Provision PostgreSQL, set `DATABASE_URL` accordingly.
2. Set `ENV=production`, `SESSION_COOKIE_SECURE=true` (requires real HTTPS).
3. Generate fresh `SECRET_KEY`/`PASSWORD_PEPPER` — do not reuse dev values.
4. Run the backend behind a reverse proxy (nginx/Caddy) that terminates
   TLS and enforces a request body size limit (see Known Limitations).
5. `npm run build && npm run start` for the frontend, or deploy to
   Vercel/similar; set `NEXT_PUBLIC_API_BASE_URL` to the real API origin.
6. Set `CORS_ALLOWED_ORIGIN` to the frontend's real production origin.
7. Adopt Alembic for schema migrations before making further schema changes.
8. Add rate limiting in front of `/api/auth/*`.
9. Wire up real Stripe integration in `services/billing_service.py` before
   claiming billing is live (it currently is not — see Section 10).
