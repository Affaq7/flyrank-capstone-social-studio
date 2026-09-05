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
