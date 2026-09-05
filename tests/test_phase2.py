from app.constraints import PROFILES, X_PROFILE, validate

SAMPLE_MARKDOWN = """
# Why Idempotent Publishing Matters

Shipping the same social post twice erodes trust faster than shipping it
late. When a worker restarts mid-batch, or a network call times out after
the platform already accepted the request, a naive retry creates a
duplicate. The fix isn't "don't crash" — it's a durable idempotency key
checked before every side effect, so retries collapse into a no-op instead
of a second post. That single design choice is worth more than any amount
of clever scheduling.
""".strip()


def test_ingest_and_generate_variants_for_all_platforms(client):
    post_response = client.post("/posts", json={"markdown": SAMPLE_MARKDOWN})
    assert post_response.status_code == 201
    post_id = post_response.json()["id"]

    variants_response = client.post(f"/posts/{post_id}/variants")
    assert variants_response.status_code == 201

    variants = variants_response.json()
    assert {v["platform"] for v in variants} == set(PROFILES.keys())

    for variant in variants:
        profile = PROFILES[variant["platform"]]
        assert len(variant["body"]) <= profile.max_length
        assert len(variant["hashtags"]) <= profile.max_hashtags
        assert variant["status"] == "draft"
        assert variant["generation_source"] in ("gemini", "template_fallback")


def test_rule_breaking_variant_is_blocked_with_named_error():
    too_long_body = "x" * (X_PROFILE.max_length + 50)
    too_many_hashtags = [f"#tag{i}" for i in range(X_PROFILE.max_hashtags + 2)]

    violations = validate(X_PROFILE, too_long_body, too_many_hashtags)
    violated_rules = {v.rule for v in violations}

    assert violated_rules == {"max_length", "max_hashtags"}
    messages = {v.rule: v.message for v in violations}
    assert f"{len(too_long_body)} > {X_PROFILE.max_length}" in messages["max_length"]
    assert f"{len(too_many_hashtags)} > {X_PROFILE.max_hashtags}" in messages["max_hashtags"]
