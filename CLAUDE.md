# Social Media Studio — Capstone

## Stack
- [Node.js + Express] or [Python + FastAPI] — pick one
- SQLite for storage
- Real publish target: [Telegram / Discord / Mastodon] — pick one

## Architecture rules
- One `SocialPublisher` interface. Implementations:
  - One real adapter (Telegram/Discord/Mastodon)
  - MockXPublisher
  - MockLinkedInPublisher
- The app must never know which platform it's publishing to — swapping
  adapters must only touch config, never business logic.

## Non-negotiable requirements
- Idempotent publish: same variant + same slot = exactly one post, even
  under retries. This is the core of the grade.
- Durable scheduling: worker restart mid-batch must not create duplicates.
- Only `approved` variants can be scheduled. Unapproved schedule attempts
  must return a 4xx with an error message.
- Constraint profiles (max length, tone, hashtag count) must be enforced
  in code — a variant that breaks a rule never reaches review.

## Conventions
- Secrets live in `.env` only, never committed. `.env.example` has
  placeholders for every variable.
- Small, focused commits — one per unit of work.
- Every requirement needs a proof in EVIDENCE.md (test output or transcript).

## Non-goals (explicitly out of scope)
- Image generation, analytics, engagement tracking
- Real Instagram/X/LinkedIn accounts (mock adapters only)