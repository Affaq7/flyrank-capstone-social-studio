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
