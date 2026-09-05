SAMPLE_MARKDOWN = """
# Why Idempotent Publishing Matters

Shipping the same social post twice erodes trust faster than shipping it
late. A durable idempotency key checked before every side effect turns a
crash-and-retry into a safe no-op.
""".strip()


def _create_variants(client):
    post_response = client.post("/posts", json={"markdown": SAMPLE_MARKDOWN})
    assert post_response.status_code == 201
    post_id = post_response.json()["id"]

    variants_response = client.post(f"/posts/{post_id}/variants")
    assert variants_response.status_code == 201
    return variants_response.json()


def test_approve_then_schedule_succeeds(client):
    variant = _create_variants(client)[0]

    approve_response = client.patch(f"/variants/{variant['id']}/approve")
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"

    schedule_response = client.post(f"/variants/{variant['id']}/schedule")
    assert schedule_response.status_code == 200
    assert schedule_response.json() == {"status": "would_schedule"}


def test_schedule_unapproved_variant_returns_4xx_with_reason(client):
    variant = _create_variants(client)[0]
    assert variant["status"] == "draft"

    schedule_response = client.post(f"/variants/{variant['id']}/schedule")
    assert schedule_response.status_code in (400, 422)
    assert "draft" in schedule_response.json()["detail"]


def test_reject_with_reason_stores_it(client):
    variant = _create_variants(client)[0]

    reject_response = client.patch(
        f"/variants/{variant['id']}/reject",
        json={"rejection_reason": "tone doesn't match brand voice"},
    )
    assert reject_response.status_code == 200
    body = reject_response.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "tone doesn't match brand voice"


def test_editing_approved_variant_resets_to_draft(client):
    variant = _create_variants(client)[0]

    approve_response = client.patch(f"/variants/{variant['id']}/approve")
    assert approve_response.json()["status"] == "approved"

    edit_response = client.patch(
        f"/variants/{variant['id']}",
        json={"body": "A manually edited, still-compliant post."},
    )
    assert edit_response.status_code == 200
    edited = edit_response.json()
    assert edited["status"] == "draft"
    assert edited["body"] == "A manually edited, still-compliant post."
