# Evidence

One proof per requirement, in the order requirements were built.

## Phase 2: Ingestion & Generation

**Requirements proven:**
1. `POST /posts` accepts a URL or pasted Markdown and stores it once in
   Postgres (`posts.content`) as the single source of truth.
2. `POST /posts/{id}/variants` generates one variant per platform (Discord,
   X-style, LinkedIn-style) reading only the stored post.
3. Constraint profiles (max length, hashtag count) are enforced in code,
   independent of what Gemini returns — a rule-breaking variant is blocked
   with an error naming the broken rule.

**Test run** (`pytest tests/test_phase2.py -v`), against the real Postgres
container from `docker-compose.yml` (port 5433 — see `BUILDLOG.md` for why),
with each test isolated in its own rolled-back transaction:

```
============================= test session starts =============================
platform win32 -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- ...\venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Haroon Traders\Desktop\Flyrank AI\Social Media Studio - Capstone
plugins: anyio-4.15.1
collecting ... collected 2 items

tests/test_phase2.py::test_ingest_and_generate_variants_for_all_platforms PASSED [ 50%]
tests/test_phase2.py::test_rule_breaking_variant_is_blocked_with_named_error PASSED [100%]

======================== 2 passed, 2 warnings in 0.05s ========================
```

- `test_ingest_and_generate_variants_for_all_platforms`: posts a Markdown
  snippet, generates variants, and asserts all three platforms
  (`discord`/`x`/`linkedin`) come back, each within its own profile's
  `max_length`/`max_hashtags`.
- `test_rule_breaking_variant_is_blocked_with_named_error`: feeds
  `constraints.validate()` (the exact function the generation pipeline calls
  before storing anything) a deliberately too-long body with too many
  hashtags, and asserts the violations name the specific broken rules
  (`max_length`, `max_hashtags`) with the offending numbers in the message.

**Manual sanity check** (real server, not the test suite): `GEMINI_API_KEY`
is not yet set in `.env`, so the generation pipeline's error→fallback path is
what actually ran —

```
POST /posts {"markdown": "# Test Post\n\nIdempotent publishing means retries never create duplicates..."}
→ 201 {"id": "038277bf-...", "source_type": "markdown", ...}

POST /posts/038277bf-.../variants
→ 201 [
    {"platform": "discord",  "generation_source": "template_fallback", ...},
    {"platform": "x",        "generation_source": "template_fallback", ...},
    {"platform": "linkedin", "generation_source": "template_fallback", ...}
  ]
```

All three variants respected their platform's profile (each `body` truncated
to that platform's `max_length`, each `hashtags` list capped to
`max_hashtags`), proving the fallback path — not just the Gemini path — is
independently constraint-safe. The manual test row was deleted afterward; it
never went through the transaction-rollback test fixture.

## Phase 3: Review Workflow

**Requirements proven:**
1. `PATCH /variants/{id}/approve` sets status to `approved`.
2. `PATCH /variants/{id}/reject` sets status to `rejected` and stores an
   optional `rejection_reason`.
3. `PATCH /variants/{id}` edits body/hashtags and resets status to `draft` —
   even from `approved`, since edited content needs re-review.
4. `POST /variants/{id}/schedule` is gated on `approved` status — a
   non-approved variant is rejected with a 422 naming the actual status.

**Test run** (`pytest tests/test_phase3.py -v`):

```
============================= test session starts =============================
platform win32 -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- ...\venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Haroon Traders\Desktop\Flyrank AI\Social Media Studio - Capstone
plugins: anyio-4.15.1
collected 4 items

tests/test_phase3.py::test_approve_then_schedule_succeeds PASSED                [ 25%]
tests/test_phase3.py::test_schedule_unapproved_variant_returns_4xx_with_reason PASSED [ 50%]
tests/test_phase3.py::test_reject_with_reason_stores_it PASSED                  [ 75%]
tests/test_phase3.py::test_editing_approved_variant_resets_to_draft PASSED      [100%]

======================== 4 passed, 2 warnings in 16.56s ========================
```

- `test_approve_then_schedule_succeeds`: approves a variant, then schedules
  it with a real future `scheduled_time` (the endpoint's shape as of Phase 5
  — see `BUILDLOG.md`), asserting `200`/`status: "scheduled"`.
- `test_schedule_unapproved_variant_returns_4xx_with_reason`: schedules a
  still-`draft` variant, asserts `422` naming `"draft"`.
- `test_reject_with_reason_stores_it`: rejects with a reason, asserts it's
  stored and echoed back in the response.
- `test_editing_approved_variant_resets_to_draft`: approves a variant, edits
  its body, asserts `status` drops back to `draft`.

**Note on this test file's history:** these tests were originally written
against Phase 3's `/schedule` stub, which took no request body. When Phase 5
made `/schedule` real (requiring `scheduled_time`), both schedule-related
tests broke — not an application bug, but a stale test contract calling an
endpoint whose shape had since changed. Fixed by supplying a real future
`scheduled_time` in both. Full story in `BUILDLOG.md`.

## Phase 4: Adapters and Idempotent Publish

**Requirements proven:**
1. `POST /variants/{id}/publish` requires `approved` (or `published`, for a
   legitimate repeat call) status — 4xx otherwise.
2. Publishing routes through the correct platform adapter via the
   `SocialPublisher` interface, with the endpoint never branching on
   platform name.
3. **The core idempotency guarantee**: calling `/publish` twice for the same
   variant produces exactly one successful `publish_attempts` row and
   exactly one real adapter call — not two.
4. A failed publish attempt can be legitimately retried, updating the same
   `publish_attempts` row rather than inserting a second one under the same
   `idempotency_key`.
5. Swapping which adapter a platform maps to is a config-only change — zero
   business logic touches it.

**Test run** (`pytest tests/test_phase4.py -v`):

```
============================= test session starts =============================
platform win32 -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- ...\venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Haroon Traders\Desktop\Flyrank AI\Social Media Studio - Capstone
plugins: anyio-4.15.1
collected 5 items

tests/test_phase4.py::test_publish_approved_variant_succeeds_via_correct_adapter PASSED [ 20%]
tests/test_phase4.py::test_publish_unapproved_variant_returns_4xx PASSED        [ 40%]
tests/test_phase4.py::test_repeated_publish_call_creates_exactly_one_success_record PASSED [ 60%]
tests/test_phase4.py::test_retry_after_failed_publish_updates_existing_attempt_row PASSED [ 80%]
tests/test_phase4.py::test_adapter_swap_changes_nothing_but_config PASSED       [100%]

======================== 5 passed, 2 warnings in 17.44s ========================
```

- `test_publish_approved_variant_succeeds_via_correct_adapter`: publishes a
  `linkedin` variant, asserts `MockLinkedInPublisher`'s own log recorded it
  — proves routing landed on the right adapter, not just that publishing
  "worked."
- `test_publish_unapproved_variant_returns_4xx`: publishes a `draft`
  variant, asserts `422` naming `"draft"`.
- `test_repeated_publish_call_creates_exactly_one_success_record` — the
  project's most important test. Calls `/publish` twice for the same
  variant; a spy on the adapter itself proves it was only called **once**
  (not inferred from the database alone), and exactly one `success` row
  exists in `publish_attempts`.
- `test_retry_after_failed_publish_updates_existing_attempt_row`: a variant
  whose first publish attempt fails, then succeeds on retry, ends up with
  exactly **one** `publish_attempts` row total (updated in place), not two
  — proving the unique `idempotency_key` constraint's retry path works as
  designed rather than throwing an `IntegrityError` on the second attempt.
- `test_adapter_swap_changes_nothing_but_config`: swaps which adapter the
  `discord` platform maps to (a mock instead of the real `DiscordPublisher`)
  purely via the `get_adapters` dependency — the same endpoint code path
  publishes successfully through the swapped-in adapter, proving zero
  business logic branches on platform identity.

**Note on this test file's history:** the two count-based assertions
(`test_repeated_publish_call...`, `test_retry_after_failed_publish...`)
originally queried `publish_attempts` table-wide rather than scoped to the
variant each test created. Against the shared dev database's real leftover
rows from manual testing, that produced `5` instead of `1` — not an
application bug (the adapter-call-count and retry-count assertions, which
*were* correctly scoped, passed throughout). Fixed by scoping both counts to
the specific variant under test. Full story in `BUILDLOG.md`.

## Phase 5: Scheduling, History, and Hardening

**Requirements proven:**
1. `POST /variants/{id}/schedule` accepts a real future `scheduled_time`
   and rejects a past one with a 422 naming the reason.
2. A worker (`worker_main.py`, APScheduler + Postgres-backed
   `SQLAlchemyJobStore`) polls for due, `approved` slots and publishes them
   through the exact same idempotent code path (`publish_variant_now`) the
   manual endpoint uses.
3. `GET /publish-history` returns every `publish_attempts` row with full
   context (variant, platform, status, `response_detail`, `attempted_at`).
4. **Probe 5 — a worker crash mid-publish, followed by a restart, produces
   zero duplicates**, verified both automatically and by hand (below).
5. **Probe 4 — a real Discord webhook fires and `publish_attempts` records
   the platform's confirmation**, verified repeatedly across this project's
   manual testing (below) — with one real gap named honestly: the row
   confirms creation but doesn't yet store the message's own id.

**Test run** (`pytest tests/test_phase5.py -v`):

```
============================= test session starts =============================
platform win32 -- Python 3.13.7, pytest-9.1.1, pluggy-1.6.0 -- ...\venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: C:\Users\Haroon Traders\Desktop\Flyrank AI\Social Media Studio - Capstone
plugins: anyio-4.15.1
collected 9 items

tests/test_phase5.py::test_future_scheduled_slot_is_not_yet_due PASSED          [ 11%]
tests/test_phase5.py::test_due_slot_gets_published_by_worker_poll PASSED        [ 22%]
tests/test_phase5.py::test_worker_poll_never_double_publishes_same_slot PASSED  [ 33%]
tests/test_phase5.py::test_worker_restart_mid_batch_produces_zero_duplicates PASSED [ 44%]
tests/test_phase5.py::test_pending_attempt_blocks_worker_auto_retry PASSED      [ 55%]
tests/test_phase5.py::test_publish_pending_variant_returns_409 PASSED          [ 66%]
tests/test_phase5.py::test_job_store_persists_job_across_scheduler_restart PASSED [ 77%]
tests/test_phase5.py::test_publish_history_returns_attempt_with_context PASSED  [ 88%]
tests/test_phase5.py::test_schedule_rejects_past_time PASSED                   [100%]

======================== 9 passed, 2 warnings in 20.88s ========================
```

`test_worker_restart_mid_batch_produces_zero_duplicates` is the automated
proxy for Probe 5 (simulated within one test, not a real killed process).
`test_pending_attempt_blocks_worker_auto_retry` and
`test_publish_pending_variant_returns_409` prove the `pending`-row fix in
isolation. The real proof of both probes is the manual verification below.

### Probe 5 — manual crash-safety verification (the real proof)

A manual crash test had already happened earlier in this project's
finalization: a batch of variants was scheduled and the worker killed
mid-publish. Variant `4d976679-886f-468c-9d3d-254db4a7e717` (`discord`
platform) was caught exactly in the crash window the Phase 5 design
narrows-but-doesn't-eliminate — the real Discord message had already been
sent (independently confirmed in the channel) when the process died before
its `publish_attempts` row could be updated from `pending` to `success`.

**Verifying the pending row correctly blocks auto-retry**, this session:
confirmed the row's state directly against Postgres —

```
id: e528b2f8-c2f9-4eda-89bc-ed30623ce860
slot_id: cb869813-a3ac-47ff-b4f2-6f9848ef0917
idempotency_key: 4d976679-886f-468c-9d3d-254db4a7e717:cb869813-a3ac-47ff-b4f2-6f9848ef0917
status: pending
attempted_at: 2026-09-05 23:04:15.365311+00
```

— then ran `worker_main.py` for real (a fresh process, not the one that
crashed) against the live database, which also had 5 other legitimately due
variants scheduled. Its log:

```
2026-09-06 04:17:58,616 INFO worker: Poll: found 6 due slot(s)
2026-09-06 04:17:58,616 INFO worker: Publishing variant 4d976679-886f-468c-9d3d-254db4a7e717 (discord)...
2026-09-06 04:17:58,619 WARNING worker: Skipping slot cb869813-a3ac-47ff-b4f2-6f9848ef0917: publish attempt for
    4d976679-886f-468c-9d3d-254db4a7e717:cb869813-a3ac-47ff-b4f2-6f9848ef0917 is 'pending' from a previous run
    with unknown outcome; refusing to auto-retry — needs manual reconciliation, not auto-retrying
2026-09-06 04:17:58,616 INFO worker: Publishing variant 6da0920f-b904-44b3-ae31-519da461c2fd (x)...
2026-09-06 04:17:58,637 INFO worker:   -> success
... (26491679/linkedin, 29dcf631/discord, 048bf582/x, a044ad0e/linkedin — each "-> success")
```

A **second** poll cycle five seconds later found the pending variant due
again (it's still `approved`, its slot's `scheduled_time` is still in the
past) and logged the identical skip-with-warning a second time — proving
this isn't a one-off, the block holds on every poll:

```
2026-09-06 04:18:43,587 INFO worker: Poll: found 1 due slot(s)
2026-09-06 04:18:43,587 INFO worker: Publishing variant 4d976679-886f-468c-9d3d-254db4a7e717 (discord)...
2026-09-06 04:18:43,589 WARNING worker: Skipping slot cb869813-a3ac-47ff-b4f2-6f9848ef0917: ... refusing to auto-retry
```

**Confirmed via `GET /publish-history` and a direct DB count** that nothing
was duplicated: `publish_attempts` went from 14 rows (13 `success`, 1
`pending`) before this run to 19 rows (18 `success`, 1 `pending`) after —
exactly the 5 legitimately-published variants added, the pending row
completely untouched (same `id`, same `attempted_at`).

**Resolved the stuck row** per the documented recovery procedure, since the
real message was already independently confirmed to exist:

```sql
UPDATE publish_attempts SET status = 'success',
  response_detail = 'Manually confirmed - message exists in Discord'
WHERE idempotency_key = '4d976679-886f-468c-9d3d-254db4a7e717:cb869813-a3ac-47ff-b4f2-6f9848ef0917';
```

Confirmed via a fresh `GET /publish-history` call afterward: the same row
(`e528b2f8-...`) now reads `status: "success"`,
`response_detail: "Manually confirmed - message exists in Discord"` — no new
row was inserted (still 19 total, 0 `pending` remaining).

**One honest gap in this recovery, worth naming rather than glossing over:**
the SQL above only updates `publish_attempts`; the variant's own `status` in
the `variants` table remains `"approved"`, not `"published"`. This creates
no duplicate-post risk — any future poll for this variant would still hit
the now-`success` `publish_attempts` row and return immediately without
calling the adapter — but the variant's lifecycle status is left slightly
out of sync with reality until a follow-up `/publish` call (which would
itself just hit the success short-circuit and return `"approved"` unchanged,
since the endpoint doesn't currently promote a variant to `"published"` on
finding a pre-existing success — a minor, low-risk edge case in the recovery
path, not the core idempotency guarantee).

### Probe 4 — real Discord webhook confirmation

Across this project's manual testing (Phases 4 and 5), the real
`DiscordPublisher` fired repeatedly and successfully — as of this
finalization pass, `publish_attempts` contains 19 rows, all `status:
"success"`, with `discord`-platform ones consistently showing
`response_detail: "Discord message created"` (the adapter's own confirmation
string, set only after Discord's `?wait=true` response returns a real
message object). Example entries from `GET /publish-history`:

```json
{"id": "bda3e2be-004a-42dc-82b6-522ff21525c4", "response_detail": "Discord message created", "status": "success", ...}
{"id": "096b1dfa-b0b3-4e76-a70b-051373543be2", "response_detail": "Discord message created", "status": "success", ...}
{"id": "871642ae-beef-4009-aefc-90ae859467d6", "response_detail": "Discord message created", "status": "success", ...}
```

The actual Discord channel was independently checked earlier in this
session and showed real, distinct message content per variant matching
these records — confirmed by screenshot during the manual walkthrough.

**A real gap found while writing this section, not glossed over:**
`DiscordPublisher.publish()` *does* capture Discord's real message id from
the `?wait=true` response into `PublishResult.external_id` — but
`publish_variant_now` only ever persists `result.detail` (the static string
above) into `publish_attempts.response_detail`. `external_id` is computed,
then discarded; `PublishAttempt` has no column for it at all. So the
positive claim this evidence actually supports is narrower than "links to
the message": `response_detail` proves the platform confirmed message
creation (not merely "the HTTP call didn't error"), tied to a specific
`idempotency_key` — but the row does not store the message's own id, so a
reviewer can't jump from a `publish_attempts` row straight to the specific
Discord message without independently checking the channel. Closing this
fully would mean adding an `external_id` column to `PublishAttempt` and
storing `result.external_id` in `publish_variant_now` — a small, clean fix,
but out of scope for this finalization pass since it wasn't part of what
was asked here and touches the schema of a table with 19 real rows already
in it. Flagging it rather than either silently fixing it unasked or
silently overstating what's already there.
