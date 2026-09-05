# Social Media Studio — Capstone

Ingest a piece of content (a URL or pasted Markdown), generate one variant
per platform (Discord, X-style, LinkedIn-style) via the Gemini API with a
deterministic template fallback, run each variant through a human review
workflow (approve/reject/edit), then schedule and publish it — with a real
Discord webhook adapter and mock adapters for the other two platforms — in
a way that survives a worker restart without ever double-posting.

## Architecture

```
                    ┌─────────────┐
   URL / Markdown → │   /posts    │ → Postgres: posts (single source of truth)
                    └──────┬──────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │ /posts/{id}/     │  Gemini (+ template fallback) generates
                  │   variants       │  one variant per platform, each checked
                  └──────┬───────────┘  against its ConstraintProfile
                         │
                         ▼
              ┌─────────────────────┐
              │  review workflow    │  draft → approved | rejected
              │  approve/reject/    │  (editing resets to draft)
              │  edit               │
              └──────────┬──────────┘
                         │ approved
                         ▼
              ┌─────────────────────┐        ┌────────────────────────┐
              │  /schedule          │───────▶│  slots (scheduled_time) │
              │  (future time)      │        └───────────┬────────────┘
              └─────────────────────┘                    │
                         │                                │ worker polls
                         │ /publish (immediate)            │ due + approved
                         ▼                                ▼
              ┌─────────────────────────────────────────────────┐
              │            publish_variant_now (shared)          │
              │  1. write publish_attempts row as "pending"       │
              │     (committed BEFORE the network call)           │
              │  2. call SocialPublisher.publish(content, key)    │
              │  3. update the same row to success/failed         │
              └───────────────────────┬───────────────────────────┘
                                       │
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
              DiscordPublisher   MockXPublisher   MockLinkedInPublisher
              (real webhook)     (in-memory log)   (in-memory log)
```

`worker_main.py` runs `BlockingScheduler` (APScheduler) with a
**Postgres-backed job store** — the recurring "poll for due slots" job
survives a full worker process restart; each poll re-derives "what's due
right now" from `slots`/`variants` fresh, so it doesn't matter which tick
(or which process) runs it.

## Run it

**1. Start Postgres:**
```bash
docker compose up -d
```

**2. Install dependencies** (Python 3.11+, a virtualenv is recommended):
```bash
pip install -r requirements.txt
```

**3. Configure secrets:** copy `.env.example` to `.env` and fill in
`DISCORD_WEBHOOK_URL` and `GEMINI_API_KEY` (a free-tier key works — see
`GEMINI_MODEL` for the default model). Everything else in `.env.example`
already has a working local default.

**4. Run the API server:**
```bash
uvicorn app.main:app --port 8000
```
Swagger UI: `http://127.0.0.1:8000/docs`

**5. Run the scheduling worker** (separate terminal, same `.env`):
```bash
python worker_main.py
```
It polls every `WORKER_POLL_SECONDS` (default 10) for due, approved slots.

**6. Seed a sample post** (with the server running, in a third terminal):
```bash
curl -X POST http://127.0.0.1:8000/posts \
  -H "Content-Type: application/json" \
  -d '{"markdown": "# Hello World\n\nThis is a sample post to seed the system."}'
```
Copy the returned `id`, then generate its variants:
```bash
curl -X POST http://127.0.0.1:8000/posts/<id>/variants
```
This returns one variant per platform (`discord`/`x`/`linkedin`), each
`status: "draft"`. From here, use Swagger to approve one
(`PATCH /variants/{id}/approve`) and either publish it immediately
(`POST /variants/{id}/publish`) or schedule it for later
(`POST /variants/{id}/schedule` with a future `scheduled_time`) and watch
the worker pick it up.

## API summary

| Method & path | Purpose |
|---|---|
| `POST /posts` | Ingest a URL or pasted Markdown |
| `POST /posts/{id}/variants` | Generate one variant per platform |
| `PATCH /variants/{id}/approve` | Approve a variant |
| `PATCH /variants/{id}/reject` | Reject a variant (optional reason) |
| `PATCH /variants/{id}` | Edit a variant's body/hashtags (resets to draft) |
| `POST /variants/{id}/schedule` | Schedule an approved variant for a future time |
| `POST /variants/{id}/publish` | Publish an approved variant immediately |
| `GET /publish-history` | Every publish attempt ever recorded, most recent first |

## Known limitations

- **Crash-during-publish window, narrowed but not eliminated.** A
  `publish_attempts` row is written as `"pending"` and committed *before*
  the adapter is called, so a crash mid-publish leaves a durable trace
  instead of silently forgetting the attempt — on restart, a lingering
  `pending` row blocks automatic retry (the worker skips it and logs a
  warning; the manual `/publish` endpoint returns `409`) rather than
  risking a duplicate post. This narrows the danger window to the moment
  between the adapter call returning and that row being updated — it does
  not fully eliminate it, since Discord's webhook API has no idempotency-key
  support and building a reconciliation path (asking Discord whether a
  message matching a given attempt actually exists) is out of scope for
  this project. Recovery from a stuck `pending` row is manual: check the
  real platform, then `UPDATE publish_attempts SET status = 'failed' WHERE
  idempotency_key = '...'` if nothing was actually posted (normal retry
  resumes on the next poll), or leave/mark it `success` if it was.
- **Concurrent workers are not race-safe.** The idempotency mechanism
  protects a *single* worker restarting sequentially (this project's actual
  requirement). Two separate worker processes running against the same
  database at the same time could both read "not yet published" before
  either writes — this hasn't been hardened, since nothing in this project
  runs more than one worker.
- **One documented command sequence, not full containerization.** Only
  Postgres runs via `docker compose up`; the API server and worker are
  separate Python processes you start yourself (steps 4-5 above), not
  additional Docker services.
