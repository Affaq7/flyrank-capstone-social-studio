# Phase 1 Design — Constraint Profiles, Publisher Interface, Data Model

Stack assumed: **Python + FastAPI**, **PostgreSQL** storage, real adapter = **Discord**.
Mock platforms: **MockXPublisher** (X-style) and **MockLinkedInPublisher** (LinkedIn-style).

> Correction (Phase 2): this document originally assumed SQLite and an
> Instagram-style second mock, per exploratory framing at the time. CLAUDE.md
> has since locked in the real decisions — Postgres via Docker, and
> Discord + X-style + LinkedIn-style as the three platforms — and this
> document has been updated to match. The DDL and profiles below reflect
> Postgres and LinkedIn, not the original SQLite/Instagram draft.

No implementation code in this document — interfaces and schemas only, to be
implemented in later phases.

---

## 1. Constraint profiles

Each platform enforces its rules through a `ConstraintProfile`, checked at
variant-creation time (before a variant can even reach `pending_review`).
A profile is pure data — the same `SocialPublisher.validate()` contract reads
it for every platform, so adding a platform never touches business logic.

```python
class ConstraintProfile(BaseModel):
    platform: str                  # "x" | "linkedin" | "discord"
    max_length: int                # characters, after link/media placeholders expand
    max_hashtags: int
    max_media: int                 # 0 = text-only allowed
    min_media: int                 # >0 = media is mandatory
    allowed_media_types: list[str] # e.g. ["image", "video"]
    tone_notes: str                # human-readable guidance shown in review UI, not machine-enforced
```

### X-style profile

| Field | Value | Rationale |
|---|---|---|
| `max_length` | 280 | X's hard character cap |
| `max_hashtags` | 3 | No platform-enforced cap, but house style rule to prevent hashtag-stuffing; enforced in code per CLAUDE.md's "constraint profiles... must be enforced in code" |
| `max_media` | 4 | X allows up to 4 images (or 1 video) per post |
| `min_media` | 0 | Text-only posts are valid |
| `allowed_media_types` | `["image", "video"]` | |
| `tone_notes` | "Conversational, concise, can reference threads/replies" | |

```python
X_PROFILE = ConstraintProfile(
    platform="x",
    max_length=280,
    max_hashtags=3,
    max_media=4,
    min_media=0,
    allowed_media_types=["image", "video"],
    tone_notes="Conversational, concise, can reference threads/replies",
)
```

### LinkedIn-style profile

| Field | Value | Rationale |
|---|---|---|
| `max_length` | 3000 | LinkedIn's actual post character limit |
| `max_hashtags` | 5 | No platform-enforced cap, but house style rule — LinkedIn posts read as spammy past a handful of hashtags |
| `max_media` | 9 | LinkedIn's multi-image post limit |
| `min_media` | 0 | Text-only posts are the common case on LinkedIn |
| `allowed_media_types` | `["image", "video"]` | |
| `tone_notes` | "Professional, first-person insight; thought-leadership framing" | |

```python
LINKEDIN_PROFILE = ConstraintProfile(
    platform="linkedin",
    max_length=3000,
    max_hashtags=5,
    max_media=9,
    min_media=0,
    allowed_media_types=["image", "video"],
    tone_notes="Professional, first-person insight; thought-leadership framing",
)
```

### Discord profile

The real adapter needs a profile too — Discord's own constraint is structural
(a hard API limit on message content), not a style choice like the other two.

| Field | Value | Rationale |
|---|---|---|
| `max_length` | 2000 | Discord's hard cap on message content, enforced by the API itself |
| `max_hashtags` | 5 | House rule — Discord has no native hashtag feature, so this only bounds stylistic use |
| `max_media` | 10 | Attachment count we choose to support per message |
| `min_media` | 0 | Text-only messages are the default case |
| `allowed_media_types` | `["image", "video"]` | |
| `tone_notes` | "Casual, community-oriented; can reference channels/roles" | |

```python
DISCORD_PROFILE = ConstraintProfile(
    platform="discord",
    max_length=2000,
    max_hashtags=5,
    max_media=10,
    min_media=0,
    allowed_media_types=["image", "video"],
    tone_notes="Casual, community-oriented; can reference channels/roles",
)
```

**Enforcement point:** `ConstraintProfile.validate(variant) -> list[Violation]`
is called synchronously when a variant is created or edited, *before* it can
transition out of `draft`. A variant with any violation is rejected with a 4xx
and never reaches `pending_review` — satisfying "a variant that breaks a rule
never reaches review" directly.

---

## 2. `SocialPublisher` interface

The app talks to exactly one interface. Swapping `MockXPublisher` /
`MockLinkedInPublisher` / `DiscordPublisher` in and out is a config change
(which class gets instantiated for a given platform string), never a
business-logic change.

```python
from abc import ABC, abstractmethod
from pydantic import BaseModel
from enum import Enum


class PublishOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PublishRequest(BaseModel):
    idempotency_key: str      # deterministic: f"{variant_id}:{slot_id}" — see §3
    variant_id: str
    slot_id: str
    platform: str
    body: str
    media_urls: list[str]
    hashtags: list[str]


class PublishResult(BaseModel):
    outcome: PublishOutcome
    external_post_id: str | None   # platform's own id for the created post
    error_message: str | None
    was_duplicate: bool            # True if this call detected a prior successful
                                    # publish under the same idempotency_key and
                                    # short-circuited instead of posting again


class SocialPublisher(ABC):
    @abstractmethod
    def constraint_profile(self) -> ConstraintProfile:
        """Return this platform's profile, for validation before scheduling."""
        ...

    @abstractmethod
    def publish(self, request: PublishRequest) -> PublishResult:
        """
        Publish exactly once per idempotency_key, for the lifetime of the
        adapter's backing store.

        Contract (binding for every implementation, mock or real):
          - Calling publish() twice with the same idempotency_key MUST NOT
            create a second post on the platform. The second call returns
            the original PublishResult with was_duplicate=True.
          - The adapter is responsible for its own dedup ledger (keyed by
            idempotency_key) checked BEFORE performing the network
            side-effect — callers cannot be trusted to only call once,
            since the whole point is surviving retries after a crash mid-call.
          - Raises PublisherError (not platform-specific exceptions) on
            unrecoverable failure, so calling code never branches on
            platform identity.
        """
        ...

    @abstractmethod
    def get_status(self, idempotency_key: str) -> PublishResult | None:
        """Look up a prior publish attempt's result without re-publishing.
        Used by the worker on restart to reconcile in-flight attempts before
        deciding whether a retry is safe or redundant."""
        ...


class PublisherError(Exception):
    """Raised by any SocialPublisher implementation on unrecoverable failure."""
```

Why `get_status` exists as its own method rather than folding reconciliation
into `publish`: a worker resuming after a crash needs to ask "did this already
happen?" without risking a second network call if the adapter's own dedup
check has any edge case. It's the cheap, read-only half of the idempotency
contract, kept separate from the half that has side effects.

---

## 3. Data model

Five entities. Postgres DDL shown as the source of truth; a short note on
each table's role follows. (`posts`/`variants` are the two Phase 2 actually
creates — `slots`/`publish_attempts` are shown here for completeness of the
overall design but are built in the scheduling phase.)

```sql
CREATE TABLE posts (
    id            TEXT PRIMARY KEY,   -- uuid
    source_type   TEXT NOT NULL       -- "url" | "markdown"
                  CHECK (source_type IN ('url','markdown')),
    source_url    TEXT,               -- set when source_type = 'url'
    content       TEXT NOT NULL,      -- the single source of truth for generation
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE variants (
    id            TEXT PRIMARY KEY,   -- uuid
    post_id       TEXT NOT NULL REFERENCES posts(id),
    platform      TEXT NOT NULL,      -- "x" | "linkedin" | "discord"
    body          TEXT NOT NULL,
    hashtags      TEXT[] NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL       -- draft | pending_review | approved | rejected | published
                  CHECK (status IN ('draft','pending_review','approved','rejected','published')),
    generation_source TEXT NOT NULL   -- "gemini" | "template_fallback"
                  CHECK (generation_source IN ('gemini','template_fallback')),
    rejection_reason TEXT,            -- set when status = rejected (constraint or human)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE slots (
    id            TEXT PRIMARY KEY,   -- uuid
    variant_id    TEXT NOT NULL REFERENCES variants(id),
    scheduled_for TIMESTAMPTZ NOT NULL, -- when the worker should publish
    status        TEXT NOT NULL       -- pending | claimed | published | failed | cancelled
                  CHECK (status IN ('pending','claimed','published','failed','cancelled')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- one variant is published at most once per slot; a variant CAN have
    -- multiple slots (e.g. resubmitted after a failure creates a new slot,
    -- rather than mutating the old one)
    UNIQUE (variant_id, scheduled_for)
);

CREATE TABLE publish_attempts (
    id                TEXT PRIMARY KEY,   -- uuid
    variant_id        TEXT NOT NULL REFERENCES variants(id),
    slot_id           TEXT NOT NULL REFERENCES slots(id),
    idempotency_key   TEXT NOT NULL,      -- f"{variant_id}:{slot_id}", computed not stored twice
    status            TEXT NOT NULL       -- in_progress | succeeded | failed
                      CHECK (status IN ('in_progress','succeeded','failed')),
    external_post_id  TEXT,               -- set on success
    error_message     TEXT,               -- set on failure
    attempt_count     INTEGER NOT NULL DEFAULT 1,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,

    -- THE constraint that makes idempotency structural rather than
    -- best-effort: claiming a slot for publish is a single atomic INSERT
    -- guarded by this constraint, so two racing workers (or a retry after
    -- a crash) can't both believe they own the attempt.
    UNIQUE (variant_id, slot_id)
);
```

### Role of each table

- **posts** — the grouping a human authors around (a campaign/idea). Never
  published directly; exists so multiple platform variants can be traced back
  to one origin.
- **variants** — the actual publishable content, one per platform. Carries
  its own approval status; `ConstraintProfile` validation runs against a
  variant, not a post, since limits differ per platform.
- **slots** — a scheduled publish time bound to one variant. Separated from
  `variants` because a variant can be rescheduled (new slot) without losing
  its approval history, and because "durable scheduling" needs a table the
  worker can poll (`status = 'pending' AND scheduled_for <= now()`).
- **publish_attempts** — the ledger the worker writes to *before* calling
  `SocialPublisher.publish()`. The `UNIQUE (variant_id, slot_id)` constraint is
  what turns "same variant + same slot = exactly one post" from a convention
  into something the database refuses to violate, even if the worker process
  restarts mid-batch and a second worker picks up the same slot.

### Publish flow (for context, not implementation)

1. Worker polls `slots` for `status='pending' AND scheduled_for <= now()`.
2. Worker attempts `INSERT INTO publish_attempts (..., status='in_progress')`
   with `idempotency_key = f"{variant_id}:{slot_id}"`. If the unique
   constraint rejects it, another worker (or a prior crashed run) already
   owns this slot — skip.
3. On successful claim, worker calls the platform's `SocialPublisher.publish()`
   with that same `idempotency_key`, using its own dedup ledger as the second
   line of defense.
4. Worker updates the `publish_attempts` row to `succeeded`/`failed` and the
   `slots` row to `published`/`failed`.
5. On restart, any `publish_attempts` row still `in_progress` is reconciled via
   `SocialPublisher.get_status(idempotency_key)` before the worker decides
   whether to retry.

Only `approved` variants are eligible for step 1 — the worker's slot query
filters on `variants.status = 'approved'`, and any scheduling attempt against
a non-approved variant is rejected at the API layer with a 4xx before a slot
row is ever created.
