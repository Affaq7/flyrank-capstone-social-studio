# Build Log

Honest log of what AI (Claude Code) built each phase, where it helped, and
where it was wrong or needed a human call.

## Phase 1: Design

Produced `docs/DESIGN.md` — constraint profiles, the `SocialPublisher`
interface, and the data model — before the stack was fully decided.
Assumed SQLite and an Instagram-style second mock at the time, per the
options still open in `CLAUDE.md`. Both assumptions were superseded once
`CLAUDE.md`/`AGENTS.md` locked in Postgres and LinkedIn — corrected in
Phase 2, see below.

## Phase 2: Ingestion & Generation

**What was built:** `POST /posts` (URL or pasted Markdown → stored once in
Postgres), `POST /posts/{id}/variants` (one variant per platform via Gemini,
falling back to a deterministic template on API failure or a rule-breaking
response), and `app/constraints.py`'s `validate()` as the single enforcement
function used both by the generation pipeline and directly by tests.

**Where AI was wrong / needed correction:**
- Initially wrote `app/gemini_client.py` against the `google-generativeai`
  package. `pytest` immediately surfaced a `FutureWarning` that the package
  is fully end-of-life ("no longer receiving updates or bug fixes") in favor
  of `google-genai`. Caught this before it shipped and switched — worth
  flagging because it would have been an easy thing to miss if the warning
  hadn't been read.
- Assumed `docker-compose.yml`'s `5432:5432` mapping meant the container
  owned port 5432. It didn't: a native Windows PostgreSQL 18 service
  (`postgresql-x64-18`) was already bound to `0.0.0.0:5432`, so every host
  connection (`psql`, `psycopg2`, eventually the app) was silently hitting
  the *native* instance instead of the container — same port, wrong server,
  a `password authentication failed` error that had nothing to do with the
  actual password. `docker ps` showing the mapping is not proof the host
  proxy actually bound; only `Get-NetTCPConnection` cross-checked against
  the owning PID revealed the conflict.
- Stopping that native service required admin rights this session doesn't
  have (`Stop-Service` failed with "Cannot open service"). Rather than push
  for elevation, remapped the container to host port 5433
  (`docker-compose.yml` + `DATABASE_URL` in `.env`/`.env.example`) — a
  reversible, no-admin-needed fix that leaves the native Postgres install
  untouched. This is a judgment call worth revisiting if the grading
  environment doesn't have the same port conflict.
- `requirements.txt` on disk was a stray UTF-16 `pip freeze` dump from an
  unrelated environment (streamlit, xgboost, jupyter, customtkinter — none
  of it importable by this project). Replaced with an actual manifest of
  what `app/` and `tests/` import.

**Judgment calls:**
- `GEMINI_API_KEY` is not in `.env` — per direction, the real Gemini call
  path is built and wired up, but not exercised in this session's test run.
  Every variant created in `EVIDENCE.md`'s test run and manual check has
  `generation_source: "template_fallback"`. Add a working key to `.env` and
  re-run to exercise the real Gemini path; the fallback logic doesn't change
  either way.
- Tests isolate via a per-test transaction rollback (SAVEPOINT restarted
  after each `session.commit()`) rather than truncating tables before/after
  a run — chosen so tests can run in any order, in parallel, or against a
  database with real dev data, without ever needing a teardown step that
  could itself fail and leave junk behind.
- `slots`/`publish_attempts` tables are deliberately not created yet, even
  though `docs/DESIGN.md` designs all four tables — they belong to the
  scheduling phase and would be dead tables against unbuilt code right now.

## Phase 3: Review Workflow

**What was built:** `PATCH /variants/{id}/approve`, `PATCH /variants/{id}/reject`
(with an optional `rejection_reason`), `PATCH /variants/{id}` (general edit —
resets status to `draft` regardless of prior state, since edited content
needs re-review), and a `POST /variants/{id}/schedule` stub gated on
`approved` status.

**Judgment calls:**
- Editing an `approved` or `rejected` variant unconditionally resets it to
  `draft` and clears `rejection_reason` — a person changed the content, so
  any prior review decision is stale.
- The general-edit endpoint re-validates the edited `body`/`hashtags`
  against the platform's `ConstraintProfile` before saving, using the exact
  same `constraints.validate()` the generation pipeline calls — an edit that
  breaks the profile is rejected the same way a bad AI response would be.
  Editing doesn't get a free pass around enforcement.
- `/schedule` was deliberately left as a status-check-only stub in this
  phase (returning a placeholder), since real scheduling required tables and
  a worker that didn't exist yet. Became real in Phase 5 — see below, and see
  `EVIDENCE.md`'s Phase 3 section for the stale-test fallout that caused.

## Phase 4: Adapters and Idempotent Publish

**What was built:** the `SocialPublisher` interface (one abstract method:
`publish(content, idempotency_key) -> PublishResult`), a real
`DiscordPublisher` (webhook POST with `wait=true` so the response includes
the real message id), `MockXPublisher`/`MockLinkedInPublisher` (in-memory
log, no network call), a `PLATFORM_ADAPTERS` registry mapping platform name
to adapter instance, and `POST /variants/{id}/publish` — the idempotent
publish endpoint.

**Where AI was wrong / needed correction:**
- The first draft of the publish endpoint's status gate rejected anything
  that wasn't `"approved"` — including `"published"`. That meant a second
  call to `/publish` for an already-published variant hit the *gate* and
  returned a 422, never reaching the idempotency check meant to handle
  exactly that case. Caught by a failing test
  (`test_repeated_publish_call_creates_exactly_one_success_record`), not by
  reasoning about it in advance. Fixed by allowing `"published"` through the
  gate too.
- The first draft's retry logic didn't account for a previously-*failed*
  attempt at all — a second publish call after a failure would have tried
  to `INSERT` a second `publish_attempts` row under the same
  already-existing `idempotency_key`, throwing an unhandled `IntegrityError`
  on the very first manual retry. You caught this during plan review before
  any code was written; fixed via a look-up-then-branch (existing `failed`
  row → update in place; no row yet → insert) rather than a naive
  get-or-create.

**Judgment calls:**
- Adapter-side dedup was dropped in favor of endpoint-level-only dedup
  (check `publish_attempts` before calling the adapter) — simpler, matches
  this phase's literal spec, though a real reduction from the richer
  Phase 1 design sketch where each adapter kept its own ledger too.
- Mock adapters record what they'd post into a plain in-memory Python list,
  not a database table — a debug aid only; `publish_attempts` is the actual
  durable record for every adapter alike.
- Test isolation for adapter state required care: `PLATFORM_ADAPTERS` is a
  module-level singleton, so tests that need to assert on adapter call
  counts or logs use fresh, test-local adapter instances via a
  `get_adapters` dependency override, never asserting against the shared
  production singletons.

## Phase 5: Scheduling, History, and Hardening

**What was built:** real time-based scheduling (`POST /variants/{id}/schedule`
now accepts a future `scheduled_time`, replacing Phase 3's stub), a
`poll_due_slots`/`run_poll_once` worker calling the exact same
`publish_variant_now` code path the manual endpoint uses (no duplicated
idempotency logic), `worker_main.py` running APScheduler's `BlockingScheduler`
with a Postgres-backed `SQLAlchemyJobStore` (not the default in-memory
store), and `GET /publish-history` for a full audit trail.

**The most important judgment call this project made:** a real gap was
found during plan review, before this phase's code was written — the
original design only wrote a `publish_attempts` row *after* the adapter call
returned. A crash between the real Discord post succeeding and that row
being written would leave no trace at all; on restart, the next poll would
see "not yet published" and call the adapter again — a genuine duplicate,
and the literal thing this phase's gate is about, not the multi-worker race
already flagged as out of scope.

**Fix:** a `publish_attempts` row is now written with `status = "pending"`
and *committed immediately, before* the adapter is ever called; only then is
the adapter invoked, and the same row updated afterward to `success`/
`failed`. On restart, a lingering `pending` row raises `PublishPendingError`
instead of being silently retried — the worker skips it and logs a warning;
the manual `/publish` endpoint returns `409`. Recovery is manual: check the
real platform, then fix the row directly via `psql`.

**This narrows the crash-during-publish window — it does not eliminate
it.** A crash in the (now much smaller) window between the adapter call
returning and that row being updated still leaves an unresolved `pending`
row needing manual reconciliation. Full elimination would require either
platform-side idempotency-key support (Discord's webhook API has none) or a
reconciliation read-path (asking Discord whether a message matching a given
attempt actually exists) — both explicitly out of scope for this capstone.
This is documented plainly in `README.md`'s known-limitations section, not
silently left uncovered.

**Other judgment calls:**
- One recurring polling job, not one APScheduler job per slot — every poll
  re-derives "what's due right now" from the database fresh, so it doesn't
  matter which tick or which process runs it. This is also how
  `docs/DESIGN.md` originally sketched the flow.
- Rescheduling a variant reuses its existing slot (updating `scheduled_time`
  in place) rather than creating a new one — a new slot would mint a new
  `idempotency_key` and silently defeat retry semantics for a variant being
  rescheduled after an earlier failed attempt.
- Concurrent multi-worker races remain explicitly out of scope, same
  reasoning as Phase 4's concurrent-HTTP-request gap — this project's actual
  gate is a single worker restarting sequentially, not horizontal scaling.
- README's "a stranger can run it with one command" is delivered as one
  clearly-documented sequence of commands (compose up, pip install, run
  server, run worker, seed), not full containerization of the app/worker
  themselves — this phase's own bullet list asked for multiple discrete
  steps, not a single combined command.

**Where AI was wrong / needed correction (post-implementation):**
- Two test files (`test_phase3.py`, `test_phase4.py`, both written in
  earlier phases) broke once Phase 5 shipped: `test_phase3.py`'s two
  schedule-related tests called `/schedule` with no request body, which
  Phase 5's real (body-requiring) endpoint now rejects with a 422 before
  ever reaching the status check — a stale test contract, not an
  application bug, fixed by supplying a real future `scheduled_time`.
  `test_phase4.py`'s two count-based idempotency assertions queried
  `publish_attempts` table-wide rather than scoped to the variant under
  test, and were silently passing only by luck until the shared dev
  database accumulated enough real leftover rows (from manual walkthroughs)
  to inflate the count past 1 and fail loudly. Both were caught by actually
  running the full suite at finalization, not by inspection — a reminder
  that "tests pass in isolation" and "tests pass against real accumulated
  state" are different claims.

## Finalization pass

**What happened:** closed out Probes 4 and 5 for real. A manual crash test
had already happened before this pass - a batch of Discord variants
scheduled together, the worker killed mid-publish, leaving variant
`4d976679-...` with a real Discord message already sent but its
`publish_attempts` row stuck at `"pending"`. This session: found the
FastAPI server actually running was a stale process from hours earlier
(only had the original 2 routes - Phase 3/4/5 endpoints didn't exist on it,
since it predated all that code), killed it, started a fresh one. Ran
`worker_main.py` for real against the live database, confirmed via its log
that the pending variant was skipped with a warning (not silently
re-published) across two separate poll cycles, while 5 other legitimately
due variants published normally. Confirmed via `GET /publish-history` and a
direct Postgres count that `publish_attempts` went from 14 to 19 rows -
exactly the 5 new ones, the pending row completely untouched. Resolved it
manually per the documented recovery procedure (`UPDATE ... SET
status='success'`), since the real message's existence was already
independently confirmed. Confirmed the update landed via a fresh
`GET /publish-history` call: 0 `pending` rows remaining, same row id, no
duplicate inserted.

**A real gap found while writing this up, not while coding it:** while
documenting Probe 4, noticed that `DiscordPublisher.publish()` captures
Discord's real message id into `PublishResult.external_id`, but
`publish_variant_now` never persists it - only a static confirmation
string (`result.detail`) makes it into `publish_attempts.response_detail`,
and `PublishAttempt` has no column for the id at all. So "publish_attempts
record links to the message" (the brief's literal wording for this probe)
is not fully true as built: the row proves the platform confirmed creation,
but doesn't let a reviewer jump straight from a row to the specific Discord
message. Not fixed in this pass - it wasn't part of what was asked, and it
touches the schema of a table that now has 19 real rows in it - but named
honestly in `EVIDENCE.md` rather than left as an inflated claim or silently
patched without being asked.

**Also found and fixed, unrelated to Probes 4/5:** the machine this project
runs on had accumulated roughly a dozen stale `uvicorn`/`worker_main.py`
background processes across earlier phases of this session - a recurring
consequence of Git Bash's `$!` returning an MSYS-level PID that doesn't map
to the actual Windows process, so earlier "kill this PID" attempts were
frequently no-ops. Cleaned up via PowerShell's `Get-CimInstance`/
`Stop-Process` against real Windows PIDs instead, keeping only the one
server actually needed for this pass's verification.

**One thing this pass did *not* do:** it did not re-verify the earlier
crash (the actual kill-mid-batch moment) firsthand - that had already
happened before this session started, and the pending row was the artifact
handed off to verify against. What this pass *did* independently confirm,
against the live system: the pending row correctly blocks automatic retry
(twice, across two real poll cycles), no duplicate row gets created, and
the documented manual recovery procedure works exactly as written.
