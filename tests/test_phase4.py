from unittest.mock import patch

from app.main import app
from app.models import PublishAttempt, Slot
from app.publishers.base import PublishResult
from app.publishers.mocks import MockLinkedInPublisher, MockXPublisher
from app.publishers.registry import PLATFORM_ADAPTERS, get_adapters

SAMPLE_MARKDOWN = """
# Why Idempotent Publishing Matters

Shipping the same social post twice erodes trust faster than shipping it
late. A durable idempotency key checked before every side effect turns a
crash-and-retry into a safe no-op.
""".strip()


class _FlakyPublisher:
    """Fails the first call, succeeds on every call after — simulates a
    real-world 'bad webhook URL, then fixed' retry scenario."""

    def __init__(self) -> None:
        self.calls = 0

    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        self.calls += 1
        if self.calls == 1:
            return PublishResult(success=False, external_id=None, detail="simulated failure")
        return PublishResult(success=True, external_id="ext-123", detail="simulated success")


def _create_variants(client):
    post_response = client.post("/posts", json={"markdown": SAMPLE_MARKDOWN})
    assert post_response.status_code == 201
    post_id = post_response.json()["id"]

    variants_response = client.post(f"/posts/{post_id}/variants")
    assert variants_response.status_code == 201
    return variants_response.json()


def _override_adapter(platform: str, adapter) -> None:
    app.dependency_overrides[get_adapters] = lambda: {**PLATFORM_ADAPTERS, platform: adapter}


def _success_count_for(db_session, variant_id: str) -> int:
    """Scoped to one variant, not a table-wide count — the shared dev
    database can have unrelated leftover success rows from manual testing."""
    return (
        db_session.query(PublishAttempt)
        .join(Slot, PublishAttempt.slot_id == Slot.id)
        .filter(Slot.variant_id == variant_id)
        .filter(PublishAttempt.status == "success")
        .count()
    )


def _attempt_count_for(db_session, variant_id: str) -> int:
    return (
        db_session.query(PublishAttempt)
        .join(Slot, PublishAttempt.slot_id == Slot.id)
        .filter(Slot.variant_id == variant_id)
        .count()
    )


def test_publish_approved_variant_succeeds_via_correct_adapter(client):
    variant = next(v for v in _create_variants(client) if v["platform"] == "linkedin")

    fresh_adapter = MockLinkedInPublisher()
    _override_adapter("linkedin", fresh_adapter)

    client.patch(f"/variants/{variant['id']}/approve")
    response = client.post(f"/variants/{variant['id']}/publish")

    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert len(fresh_adapter.log) == 1
    assert fresh_adapter.log[0]["content"] == variant["body"]


def test_publish_unapproved_variant_returns_4xx(client):
    variant = _create_variants(client)[0]
    assert variant["status"] == "draft"

    response = client.post(f"/variants/{variant['id']}/publish")
    assert response.status_code in (400, 422)
    assert "draft" in response.json()["detail"]


def test_repeated_publish_call_creates_exactly_one_success_record(client, db_session):
    variant = next(v for v in _create_variants(client) if v["platform"] == "x")
    client.patch(f"/variants/{variant['id']}/approve")

    fresh_adapter = MockXPublisher()
    _override_adapter("x", fresh_adapter)

    with patch.object(fresh_adapter, "publish", wraps=fresh_adapter.publish) as spy:
        first = client.post(f"/variants/{variant['id']}/publish")
        second = client.post(f"/variants/{variant['id']}/publish")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "published"
    assert second.json()["status"] == "published"
    # The real assertion: the adapter itself was only ever called once.
    # Checking the DB alone wouldn't prove the second call short-circuited
    # before reaching the adapter.
    assert spy.call_count == 1

    assert _success_count_for(db_session, variant["id"]) == 1


def test_retry_after_failed_publish_updates_existing_attempt_row(client, db_session):
    variant = next(v for v in _create_variants(client) if v["platform"] == "x")
    client.patch(f"/variants/{variant['id']}/approve")

    flaky_adapter = _FlakyPublisher()
    _override_adapter("x", flaky_adapter)

    first = client.post(f"/variants/{variant['id']}/publish")
    assert first.status_code == 200
    assert first.json()["status"] == "approved"  # publish failed, not published

    second = client.post(f"/variants/{variant['id']}/publish")
    assert second.status_code == 200
    assert second.json()["status"] == "published"

    assert flaky_adapter.calls == 2

    # Exactly one publish_attempts row total for this variant's key — the
    # retry updated the existing row instead of inserting a second one
    # under the same unique idempotency_key.
    assert _attempt_count_for(db_session, variant["id"]) == 1


def test_adapter_swap_changes_nothing_but_config(client):
    variant = next(v for v in _create_variants(client) if v["platform"] == "discord")
    client.patch(f"/variants/{variant['id']}/approve")

    # Standing in for changing PLATFORM_ADAPTERS["discord"] from
    # DiscordPublisher() to a mock — the one-line config swap.
    swapped_adapter = MockXPublisher()
    _override_adapter("discord", swapped_adapter)

    response = client.post(f"/variants/{variant['id']}/publish")

    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert len(swapped_adapter.log) == 1
