# Social Media Studio — Capstone

## Stack (decided, do not deviate)
- Python + FastAPI
- PostgreSQL via Docker (not SQLite)
- Scheduler: APScheduler with a persistent job store backed by Postgres
  (never the default in-memory store — must survive restarts)
- Real publish target: Discord webhook
- Variant text: Gemini API free tier (fallback to hand-written templates
  if the API errors or quota runs out)

## Platforms (exactly 3, all need constraint profiles)
1. Discord — real adapter (webhook POST)
2. X-style — MockXPublisher (mock)
3. LinkedIn-style — MockLinkedInPublisher (mock)

## Data model (entities)
- posts: the ingested source (URL or Markdown), single source of truth
- variants: one per platform per post, status = draft/approved/rejected/published
- slots: scheduled time for a variant to publish
- publish_attempts: one row per attempt, links to variant+slot, records result

## Ingestion rule
Post enters as URL or pasted Markdown, gets stored once in Postgres.
All variant generation reads ONLY from the stored post — never
re-fetches or re-parses the original source.

## Variant generation
Call the Gemini API free tier to generate variant text per platform,
using each platform's constraint profile as part of the prompt (max
length, tone, hashtag count). If the API call fails or the response
still breaks the profile, fall back to a hand-written template — never
let a bad AI response reach review unchecked.

## Architecture rules
- One `SocialPublisher` interface. The app must never know which
  platform it's publishing to — swapping adapters changes config only,
  never business logic.
- Constraint profiles (max length, tone, hashtag count) are enforced in
  code, independent of what the AI produces. A variant that breaks a
  rule is blocked before it reaches review, with an error message
  naming the broken rule.

## Idempotency (the core of the grade)
Every variant+slot pair gets a unique idempotency key (store it in
publish_attempts, unique constraint in Postgres). The publisher checks
this key before calling the Discord webhook. A repeated publish call
for the same key must result in exactly one successful post — not an
error, not a duplicate webhook call. Verify with a test that calls
publish twice for the same variant+slot and checks publish_attempts
has exactly one success.

## Review workflow
Statuses: draft → approved | rejected → published.
Only `approved` variants can be scheduled. A schedule attempt on a
non-approved variant returns 4xx with an error naming the reason.

## Durable scheduling
APScheduler's job store is Postgres-backed, not in-memory. A worker
that stops mid-batch must resume without creating duplicate publishes
— rely on the idempotency key, not "don't crash."

## Acceptance probes (what will actually be tested)
1. Ingest a post → variants generated for all 3 platforms via Gemini, each passes its constraint profile
2. A rule-breaking variant is blocked pre-review with a named error
3. Scheduling an unapproved variant returns 4xx
4. Approve + schedule → real Discord webhook fires, publish_attempts record links to the message
5. Kill worker mid-publish, restart → history shows exactly one success, no dupes
6. Swap adapter in config (e.g. discord → mock_x) → same campaign publishes via mock, zero code changes outside adapters

## Required files
- README.md — what it does, architecture diagram, exact run/seed steps
  (must include `docker compose up` for Postgres)
- EVIDENCE.md — one proof per requirement (test output or transcript)
- BUILDLOG.md — honest AI usage log: where AI helped, where it was wrong
- .env.example — placeholders for: DATABASE_URL, DISCORD_WEBHOOK_URL, GEMINI_API_KEY

## Conventions
- Secrets in `.env` only, never committed
- Small, focused commits, one per unit of work
- After each feature, write its EVIDENCE.md proof before moving on
- After each phase, append an honest entry to BUILDLOG.md summarizing
  what was built and any judgment calls made