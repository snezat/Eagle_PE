# AGENTS.md

This file is the working guide for coding agents and contributors in this repository. It applies to the entire project.

## Project overview

ARCA Strength is a privacy-sensitive strength-planning and workout-tracking application for a school Athletic PE program. It is a server-rendered Flask application with a coach interface, a student portal, an encrypted SQLite data store, roster import tooling, and hardened Ubuntu/Debian LXC deployment scripts.

The Flask implementation in this repository is the production source of truth. Preserve the privacy, authentication, data-integrity, and deployment guarantees described here and in `README.md`.

## Repository map

- `app.py`: Flask application factory, request security gates, coach/student routes, API validation, and update controls.
- `db.py`: SQLite schema, initialization/migrations, encrypted persistence, state validation, account management, attendance, assignments, and student workout operations.
- `security.py`: key-file management, authenticated field encryption, keyed lookups, and setup-token creation.
- `roster_import.py`: parsing and merging the `.xlsx` master roster.
- `sport_names.py`: canonical sport names and migrations for legacy names.
- `templates/`: Jinja pages for login/setup, the coach app, student portal, and errors.
- `static/js/app.js`: coach-side state, rendering, and API interactions.
- `static/js/student.js`: student portal state, rendering, and API interactions.
- `static/css/app.css`: shared application styling and responsive layouts.
- `tests/`: pytest coverage for security, state/database behavior, roster imports, sport normalization, UI parity, and deployment-script invariants.
- `scripts/`: backup, import, preview, updater, and verification utilities.
- `deploy/`: LXC installer, systemd units, and optional Caddy example.
- `start.sh`: local/server launcher and first-administrator setup workflow.
- `wsgi.py`: Gunicorn entry point.

## Local development

Use a virtual environment and install development dependencies:

```sh
python -m venv .venv
# Windows PowerShell
.venv\Scripts\python -m pip install -r requirements-dev.txt
# Linux/macOS
.venv/bin/python -m pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env` when using `start.sh`, or export the equivalent environment variables. For direct local Flask development, the important safe defaults are:

```text
ARC_ENV=development
ARC_INSTANCE_PATH=instance
ARC_DATABASE_PATH=instance/arc-strength.sqlite3
ARC_TRUSTED_HOSTS=localhost,127.0.0.1
ARC_SECURE_COOKIES=0
ARC_PROXY_COUNT=0
ARC_ENABLE_TEST_STUDENT=1
```

Run the development server with `python app.py`. It listens on `127.0.0.1:8000`. On Linux, `./start.sh` also provisions dependencies and launches Gunicorn; when run as root outside `/opt/arc-strength`, it intentionally invokes the LXC installer.

Do not use production data for routine development. Tests create isolated temporary instance directories and databases.

## Required verification

Run the full suite before submitting changes:

```sh
python -m pytest
```

When working in a checkout that already tracks bytecode artifacts, set `PYTHONDONTWRITEBYTECODE=1` to avoid unrelated `.pyc` changes. Useful focused commands are:

```sh
python -m pytest tests/test_security.py
python -m pytest tests/test_roster_import.py
python -m pytest tests/test_sport_names.py
python -m pytest tests/test_parity.py
```

Match tests to the changed surface:

- Authentication, sessions, headers, CSRF, account controls, routes, or request validation: update/run `tests/test_security.py`.
- Database shape, whole-state saves, attendance, assignments, prescriptions, or student workflows: add a regression test, normally in `tests/test_security.py` or a new focused module.
- Master-roster parsing or merge semantics: update/run `tests/test_roster_import.py`.
- Sport aliases/canonicalization or startup migration: update/run `tests/test_sport_names.py`.
- Templates, public assets, deployment files, `start.sh`, updater, backup, or preview behavior: update/run `tests/test_parity.py`.

Tests should use `tmp_path` and an explicit `TESTING` configuration. Never make tests depend on the checked-in `instance/` database or keys.

## Security and privacy invariants

Treat these as hard requirements, not optional cleanup:

- All coach pages and coach APIs require an authenticated administrator session. All student pages and student APIs require an authenticated student session scoped to its linked athlete.
- Every state-changing `POST`, `PUT`, `PATCH`, or `DELETE` request must pass the existing CSRF gate. Browser requests send the token as form field `csrf_token` or header `X-CSRF-Token`.
- Keep cookies `HttpOnly` and `SameSite=Strict`; production behind HTTPS must enable secure cookies. Do not weaken trusted-host validation or proxy-hop limits.
- Preserve the security headers and no-store behavior installed by `after_request` in `app.py`.
- Never place roster data, credentials, decrypted student data, key material, or setup tokens in public templates, JavaScript bundles, CSS, logs, URLs, local storage, session storage, or error messages.
- Sensitive fields stay encrypted at rest with `FieldCipher`. Lookup-only values use the keyed lookup/digest functions. Passwords must remain one-way Werkzeug scrypt hashes and must never be encrypted reversibly.
- Use constant-time comparison for tokens or secrets where applicable. Avoid account-enumeration differences in login behavior.
- Maintain login throttling, server-side session expiry, user-agent binding, session invalidation after password/account changes, and audit logging for sensitive actions.
- Validate request types, lengths, identifiers, dates, numeric ranges, and relationships on the server. Client-side validation is only a usability aid.
- Do not enable `ARC_TRUSTED_HOSTS=*` in production, expose direct HTTP port 8000 to the public internet, or trust proxy headers unless traffic actually comes through the configured proxy.
- `instance/`, database backups, `.env`, and files such as `master.key`, `lookup.key`, `flask-secret.key`, `old-master.keys`, and `setup-token` are sensitive operational material. Do not inspect, modify, copy, print, or commit their contents as part of unrelated work.
- If database contents must be backed up or migrated, the database and matching encryption keys must move together and remain access-restricted.

If a requested change conflicts with one of these guarantees, stop and surface the conflict instead of silently relaxing the protection.

## Database and state rules

- `Database.initialize()` owns schema creation and startup migrations. Schema changes must be idempotent and safe for an existing encrypted production database.
- Never drop or overwrite user data during startup. Preserve stable athlete, account, assignment, prescription, and attendance identities during migrations and imports.
- Use `Database.connection()` for scoped access and `Database.transaction()` for multi-statement mutations that must be atomic.
- Keep encrypted columns and keyed lookup columns paired correctly. Do not query encrypted ciphertext as if it were stable searchable text.
- Whole-planner saves use optimistic concurrency through the state revision. Preserve `StateConflictError`/HTTP 409 behavior so stale browsers cannot overwrite newer data.
- Attendance uses its narrow atomic endpoint so multiple devices can record attendance safely. Do not fold attendance taps into a broad read-modify-write state save.
- Run incoming planner state through the existing validation path before persistence. Invalid references or malformed values must fail without partially replacing valid state.
- Create a database backup before a destructive roster merge. Roster matching must prefer deterministic identity preservation; ambiguity is an error, not permission to guess.
- Student and coach max changes operate on the same athlete record. Keep prescriptions, projections, submissions, and history internally consistent when changing those workflows.
- Canonicalize sport names through `sport_names.py`; do not scatter new alias handling across views or SQL.

## Flask/API conventions

- Add routes inside `create_app()` and use the existing `login_required` or `student_required` decorator as appropriate.
- JSON APIs return a JSON error object with a meaningful 4xx status for client errors. Do not leak stack traces, secrets, raw SQL, or decrypted records.
- Parse JSON with `request.get_json(silent=True)` and confirm the expected container/type before reading values.
- Limit path identifiers and uploaded content before passing them to persistence code. Roster uploads are `.xlsx` only and the app-wide upload cap is 8 MiB.
- Use UTC-aware timestamps and the `utcnow()` helper for stored timestamps. Workout/calendar dates remain ISO `YYYY-MM-DD` values.
- Keep `/healthz` cheap and free of private data.
- Use the existing audit mechanism for authentication, administrator/account changes, roster application, student record changes, and other security-relevant mutations.

## Frontend conventions

- The frontend is plain JavaScript and Jinja; there is no bundler or framework build step.
- Keep DOM selectors in sync with template IDs. `tests/test_parity.py` deliberately guards important IDs, feature hooks, and safety wording.
- Escape all user-controlled strings before inserting HTML. Reuse the existing `escapeHtml` helpers and prefer `textContent` when markup is unnecessary.
- Include `X-CSRF-Token`, `Accept: application/json`, and the correct `Content-Type` on state-changing fetch requests.
- Preserve the revision returned by `/api/state` and send it back on whole-state updates.
- Keep responsive coach, TV/weight-room, attendance, and mobile student views working. A change to one view should not remove parity features from another.
- Do not add third-party CDNs, inline executable scripts, analytics, or remote assets without explicitly reviewing the Content Security Policy and student-data implications.
- Do not persist decrypted planner or student state in browser storage.

## Roster imports

- Master roster workbooks must contain the expected `Master Roster` sheet and are parsed in memory by `roster_import.py`.
- Keep preview read-only. Only the explicit apply action may mutate state, and it must re-evaluate/validate the merge and create a backup.
- Preserve existing IDs, accounts, workout history, projected maxes, and overrides for matched athletes.
- Preserve the documented priority of a supplied `New` max over `Original`; `Original` only fills an empty stored max.
- Parenthetical name notes are aliases only when they produce one unambiguous existing match.
- Conflicts block the import. Do not invent fuzzy matching rules that can merge two students.
- Never write uploaded plaintext roster files under `static/`, add their contents to fixtures, or retain them longer than required for the request.

## Deployment and operations

- The supported production target is an unprivileged Ubuntu/Debian LXC using Gunicorn and systemd. Application code lives at `/opt/arc-strength`; private state lives at `/var/lib/arc-strength`.
- The `arcstrength` service account owns private state but not application code. Preserve root ownership of deployed code and the systemd sandboxing directives.
- Keep `deploy/install-lxc.sh` idempotent. It may repair an installation, but it must not overwrite an existing production database.
- Shell scripts are POSIX `sh`, use `set -eu`, set a known `PATH`, quote variables, and fail with actionable messages. Do not introduce Bash-only syntax unless the script shebang and all callers are deliberately changed.
- `scripts/update-app.sh` is root-run and security-sensitive. Preserve HTTPS repository validation, the single-update lock, rollback copy, dependency/startup checks, service health check, and automatic rollback.
- The web process may only create the fixed update-request file; it must never execute update commands or write application code directly.
- Keep backups atomic and restrictive (`0700` directories, `0600` data/key files). Use SQLite's backup facility rather than copying a live database file.
- Changes to `start.sh`, `deploy/`, updater, or backup scripts require the parity safety tests and careful review of ownership, permissions, failure paths, and rollback behavior.

## Change discipline

- Read the relevant tests and nearby code before editing. Prefer the smallest coherent change that preserves current behavior outside the request.
- Keep business rules in Python/database modules rather than duplicating them in JavaScript. The server is authoritative.
- Add a regression test for every bug fix and tests for new security or persistence behavior.
- Update `README.md`, `.env.example`, and this file when commands, environment variables, architecture, deployment behavior, or contributor expectations change.
- Do not commit generated caches, virtual environments, local databases, key files, setup tokens, backup archives, update status/request files, or plaintext roster exports.
- Preserve unrelated working-tree changes. A PR should contain only files needed for its stated purpose.
- Use clear commit subjects in the imperative mood and include the verification performed in the PR description.

## Completion checklist

Before considering a change complete:

1. Confirm no secret, student record, credential, database, key, or generated artifact entered the diff.
2. Review authentication, authorization, CSRF, validation, encryption, concurrency, and audit effects for the changed path.
3. Run focused tests during development and the full `python -m pytest` suite before handoff.
4. Inspect `git diff --check` and the final diff for accidental or unrelated changes.
5. For deployment changes, verify idempotence, ownership/modes, health checks, failure behavior, and rollback behavior.
6. Update documentation and configuration examples when user-facing or operational behavior changed.
