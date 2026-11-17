# OptiSynth: deployment and operations notes

Written from the Level 5 security and deployment review. Each statement says whether it was **verified** (run against
the code, in the review environment) or is **not verified** (a recommendation).

## 1. Configuration that must be set

| Setting | Required value |
|---|---|
| `ENV` | `production`. Anything else leaves API documentation pages public and puts internal exception text in error responses. The app logs a warning at start-up if `ENV` is not `production` but the database is not SQLite or the frontend origin is not local. |
| `SECRET_KEY`, `PASSWORD_PEPPER` | At least 32 characters each, and different from each other. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`. With `ENV=production` the app refuses to start otherwise. Changing `PASSWORD_PEPPER` locks every user out; changing `SECRET_KEY` signs everyone out. |
| `SESSION_COOKIE_SECURE` | `true` (enforced in production). Serve only over HTTPS. |
| `CORS_ALLOWED_ORIGIN` | The exact frontend origin, for example `https://app.example.com`. One value, no wildcard. |
| `DATABASE_URL` | PostgreSQL for anything beyond a single-user pilot (section 3). |
| `FRONTEND_BASE_URL` | The public frontend address (used in invitation links). |

### Uploaded-file parser limits

Reading an uploaded `.xlsx`, `.docx` or `.pdf` runs in a separate child process (`PARSE_ISOLATION=true`, the default; setting it to
`false` reads files inside the web process again and removes all of the protection below, so do not do that in production).
Settings, with their defaults: `PARSE_TIMEOUT_SECONDS=30`, `PARSE_MEMORY_MB=2048`, `MAX_CONCURRENT_PARSES=2`,
`PARSE_MAX_OUTPUT_MB=32`, `MAX_ARCHIVE_UNCOMPRESSED_BYTES=104857600`, `MAX_ARCHIVE_ENTRIES=2000`.

What each protection does, and where it is known to work:

| Protection | Linux | macOS | Windows |
|---|---|---|---|
| Child process gets no secrets and no database | yes (tested) | same code, not run | same code, not run |
| Wall-clock stop at `PARSE_TIMEOUT_SECONDS` + 5 s | yes (tested) | same code, not run | same code, not run |
| CPU-time limit of `PARSE_TIMEOUT_SECONDS` seconds | yes (tested) | requested, **not run on macOS** | **none** (no `resource` module) |
| Memory limit `PARSE_MEMORY_MB` | yes (tested at 400 MB) | **none** (macOS does not enforce it) | **none** |
| Everything the parser started is killed when it finishes **or** is stopped (its whole process group), and the pipes are drained for at most 2 s | yes (tested) | same code, not run | only the child process |
| The web process never holds more than `PARSE_MAX_OUTPUT_MB` of the child's answer, and 64 KB of its error output | yes (tested) | same code, not run | same code, not run |
| At most `MAX_CONCURRENT_PARSES` parsers at once | yes (tested) | yes | yes |

- **Run production on Linux.** There, a hostile file can use at most `PARSE_MEMORY_MB` per parser, so at most
  `MAX_CONCURRENT_PARSES` times that in total, plus the web process's own copy of the answer. When the child answers, the web
  process holds the raw text (at most `PARSE_MAX_OUTPUT_MB`) and then the decoded result, measured at about 3.6 times the text
  size, per running parser.
- **On macOS the memory of a runaway parser is limited only by the clocks.** In testing without a memory limit, a 10 KB PDF used
  about 2 GB in 31 seconds and a 51 KB PDF exceeded 8 GB. Use macOS for development only.
- **Windows is not supported and was not tested.** Treat it as having no CPU or memory protection, and no protection against
  processes the parser starts.
- **A parser that starts its own session escapes the group kill.** Killing the process group reaches everything the parser
  started except a process that deliberately leaves the group (`setsid`); only a parser compromised by a hostile file would do that.
  The web process still returns within about 2 seconds of the parser ending and keeps its parser slot free, ends its reader
  threads, refuses the file and logs a warning, but the stray process stays alive until it exits on its own. Run production inside a
  container or service whose memory and process limits you control, so such a process can be bounded and reaped.
- On Linux the group is killed while the parser's own process id is still reserved (the exit is detected without collecting it),
  so the kill cannot reach an unrelated process that reused the number. Where the operating system cannot do that
  (`waitid` with `WNOWAIT`, absent on some platforms) the kill still happens, after the parser is collected, and a group with
  no members left is a no-op; the remaining window is theoretical and was not exercised outside Linux.
- On a shared server or in a container also set a memory limit on the service itself (for example a cgroup limit): it covers
  every platform and everything else the process does.
- The answer cap is far above the largest legitimate table: 20,000 rows by 60 columns of realistic cells (a 9.3 MB workbook, the
  most the row and column limits allow) measured 17.7 MB. A file that answers with more is refused with a message
  saying so, rather than read. Do not lower `PARSE_MAX_OUTPUT_MB` below about 20 unless you also lower `MAX_UPLOAD_ROWS`.
- Very low `PARSE_MEMORY_MB` values (below the 400 MB used in testing) were not tried and may stop the parser from starting.

## 2. Running the API

- Run it behind a TLS-terminating reverse proxy that also sets `Strict-Transport-Security` (the app does not).
- Start uvicorn with `--proxy-headers --forwarded-allow-ips=<your proxy address>` so the sign-in throttle sees each
  visitor's own address rather than the proxy's. Without it every visitor shares one address and the limits are reached together.
- Use **one worker process** unless you accept weaker limits. The sign-in throttle (5 failures per address and account per
  15 minutes, 30 per address, 100 per account) keeps its counts in the process's memory, so N workers allow N times as many guesses.
- Not verified: behaviour behind a particular proxy; this repository contains no Dockerfile, process manager or CI configuration.

## 3. Database

- **Verified (PostgreSQL 16.15):** all migrations apply from empty to head; the migration-portability tests, including the
  eight PostgreSQL scenarios that are skipped by default, pass (`OPTISYNTH_TEST_POSTGRES_URL=... python3 -m unittest backend.tests.test_migration_portability`);
  and the API test modules ran against PostgreSQL.
- **Delete change cases through the application, never with raw SQL.** On PostgreSQL a raw `DELETE FROM change_cases` fails when the
  case has been ranked: `predictions.dataset_version_id` is `ON DELETE RESTRICT`, and PostgreSQL checks it before the
  candidate-to-prediction cascade has run. The application's delete removes rows in explicit dependency order and is not affected.
- SQLite is fine for one person trying the tool. It serialises writers (no busy timeout is configured), so concurrent
  users can see "database is locked" errors: use PostgreSQL for a paid pilot.

## 4. Backups and recovery

Two things hold customer data and they must be backed up **together**: the database, and the files on disk: the `data/`
directory (`data/qualification_uploads/<organisation>` holds the evidence files uploaded to a diagnostic, `data/uploads/<organisation>`
the older project datasets). The older project reports in `reports/generated` are kept and served again later, so back them
up too if you use them. Diagnostic report documents are deleted from disk once they have been downloaded.

```
pg_dump -Fc -f optisynth-$(date +%F).dump "$DATABASE_URL"            # database
tar czf files-$(date +%F).tgz data reports/generated                   # uploaded evidence and kept reports, taken at the same moment
createdb optisynth_restore && pg_restore -d optisynth_restore --no-owner optisynth-<date>.dump   # restore
```

- **Verified:** a custom-format `pg_dump` restored with `pg_restore` into an empty PostgreSQL database gave the same 21 tables (the schema now has one more, `user_sessions`),
  the same migration head (`alembic_version`) and identical data, and the restored database passed the application's start-up
  migration check. Not verified: a restore with real customer volumes, point-in-time recovery, or restoring the uploaded files.
- Neither the database nor the upload directory is encrypted by the application. Use disk or volume encryption and restrict who can read them.
- Rehearse a restore before the first paying customer, and decide how long backups are kept.

## 5. Known limits (not fixed by the review)

- **Sessions are revocable (server-side).** Each sign-in stores a row in `user_sessions`; the cookie carries that row's random id
  and is accepted only while the row is unrevoked and unexpired (`SESSION_LIFETIME_HOURS`, default 12). Signing out revokes
  that one session, so a copied cookie then gets 401; other sessions of the same or any other user stay valid. The state is in
  the database, so it survives restarts and is shared by all workers. **Upgrade note:** run `alembic upgrade head` before
  starting the new code, and expect every user to sign in once more (cookies issued before this change carry no session id and
  are refused). Expired rows are removed whenever a new session starts. Not covered: there is no "sign out everywhere" action
  or password-change route yet, and each request now costs one extra indexed database read.
- **Sign-up is open and starts a trial.** Anyone can create an organisation. Trial accounts can upload evidence and rank candidates up to their limits.
- **Billing is not live.** The Paddle code is a skeleton: no webhook route is registered and signature verification is
  deliberately unimplemented, so nothing can charge a customer. The billing page is not linked.
- The legacy API-key module (`backend/app/security/api_keys.py`) is not used by any route.
