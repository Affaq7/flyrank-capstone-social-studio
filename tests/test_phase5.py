from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.db import engine
from app.models import PublishAttempt, Slot, Variant
from app.publish_service import get_or_create_slot, publish_variant_now
from app.publishers.mocks import MockLinkedInPublisher, MockXPublisher
from app.worker import poll_due_slots


def _dummy_job() -> None:
    """A real, resolvable top-level function — APScheduler serializes jobs
    by module:function reference and can't do that for a lambda."""
    pass


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


def _past_time() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=5)


def _approve_and_schedule_directly(db_session, client, platform: str, scheduled_time: datetime):
    variant_dict = next(v for v in _create_variants(client) if v["platform"] == platform)
    client.patch(f"/variants/{variant_dict['id']}/approve")
    variant = db_session.get(Variant, variant_dict["id"])
    slot = get_or_create_slot(db_session, variant, scheduled_time)
    return variant, slot


def _safe_adapters(**overrides):
    """poll_due_slots scans the WHOLE slots/variants table, not just what a
    given test created — and the shared dev database can genuinely have
    leftover approved+due rows from manual Swagger walkthroughs. Every test
    that calls poll_due_slots must pass a fully-mocked mapping for all three
    platforms, never the real PLATFORM_ADAPTERS (whose "discord" key is a
    real DiscordPublisher) — otherwise a leftover discord-platform row could
    fire an actual webhook call during a test run."""
    adapters = {
        "discord": MockXPublisher(),
        "x": MockXPublisher(),
        "linkedin": MockLinkedInPublisher(),
    }
    adapters.update(overrides)
    return adapters


def _success_count_for(db_session, *variant_ids: str) -> int:
    """Scoped to specific variants, not a table-wide count — the shared dev
    database can have unrelated leftover success rows from manual testing."""
    return (
        db_session.query(PublishAttempt)
        .join(Slot, PublishAttempt.slot_id == Slot.id)
        .filter(Slot.variant_id.in_(variant_ids))
        .filter(PublishAttempt.status == "success")
        .count()
    )


def test_future_scheduled_slot_is_not_yet_due(client, db_session):
    variant_dict = next(v for v in _create_variants(client) if v["platform"] == "x")
    client.patch(f"/variants/{variant_dict['id']}/approve")

    future_time = datetime.now(timezone.utc) + timedelta(hours=1)
    schedule_response = client.post(
        f"/variants/{variant_dict['id']}/schedule",
        json={"scheduled_time": future_time.isoformat()},
    )
    assert schedule_response.status_code == 200

    fresh_adapter = MockXPublisher()
    touched = poll_due_slots(db_session, _safe_adapters(x=fresh_adapter))

    assert touched == []
    variant = db_session.get(Variant, variant_dict["id"])
    assert variant.status == "approved"
    assert len(fresh_adapter.log) == 0


def test_due_slot_gets_published_by_worker_poll(client, db_session):
    variant, _slot = _approve_and_schedule_directly(db_session, client, "x", _past_time())

    fresh_adapter = MockXPublisher()
    touched = poll_due_slots(db_session, _safe_adapters(x=fresh_adapter))

    assert len(touched) == 1
    assert touched[0].status == "published"
    assert _success_count_for(db_session, variant.id) == 1


def test_worker_poll_never_double_publishes_same_slot(client, db_session):
    variant, _slot = _approve_and_schedule_directly(db_session, client, "x", _past_time())

    fresh_adapter = MockXPublisher()
    adapters = _safe_adapters(x=fresh_adapter)

    with patch.object(fresh_adapter, "publish", wraps=fresh_adapter.publish) as spy:
        poll_due_slots(db_session, adapters)
        poll_due_slots(db_session, adapters)  # "restart"'s first poll, same due window

    assert spy.call_count == 1
    assert _success_count_for(db_session, variant.id) == 1


def test_worker_restart_mid_batch_produces_zero_duplicates(client, db_session):
    fresh_adapter = MockXPublisher()
    adapters = _safe_adapters(x=fresh_adapter)

    variant_1, slot_1 = _approve_and_schedule_directly(db_session, client, "x", _past_time())
    variant_2, _slot_2 = _approve_and_schedule_directly(db_session, client, "x", _past_time())
    variant_3, _slot_3 = _approve_and_schedule_directly(db_session, client, "x", _past_time())

    # Simulate "the old process got through variant 1 before dying."
    publish_variant_now(db_session, variant_1, slot_1, adapters)

    with patch.object(fresh_adapter, "publish", wraps=fresh_adapter.publish) as spy:
        # Simulate "the restarted process's first poll."
        poll_due_slots(db_session, adapters)

    # Only variants 2 and 3 should have gone through the adapter during the
    # "restart" poll — variant 1 was already done and must not be touched again.
    assert spy.call_count == 2

    for variant in (variant_1, variant_2, variant_3):
        db_session.refresh(variant)
        assert variant.status == "published"

    assert _success_count_for(db_session, variant_1.id, variant_2.id, variant_3.id) == 3


def test_pending_attempt_blocks_worker_auto_retry(client, db_session):
    variant, slot = _approve_and_schedule_directly(db_session, client, "x", _past_time())

    # Simulate a crash right after the 'pending' row's first commit, before
    # the adapter call resolved.
    idempotency_key = f"{variant.id}:{slot.id}"
    db_session.add(
        PublishAttempt(
            slot_id=slot.id,
            idempotency_key=idempotency_key,
            status="pending",
            response_detail=None,
        )
    )
    db_session.commit()

    fresh_adapter = MockXPublisher()
    adapters = _safe_adapters(x=fresh_adapter)

    with patch.object(fresh_adapter, "publish", wraps=fresh_adapter.publish) as spy:
        touched = poll_due_slots(db_session, adapters)

    assert touched == []
    assert spy.call_count == 0

    db_session.refresh(variant)
    assert variant.status == "approved"

    attempt = (
        db_session.query(PublishAttempt)
        .filter(PublishAttempt.idempotency_key == idempotency_key)
        .one()
    )
    assert attempt.status == "pending"


def test_publish_pending_variant_returns_409(client, db_session):
    variant, slot = _approve_and_schedule_directly(db_session, client, "x", _past_time())

    idempotency_key = f"{variant.id}:{slot.id}"
    db_session.add(
        PublishAttempt(
            slot_id=slot.id,
            idempotency_key=idempotency_key,
            status="pending",
            response_detail=None,
        )
    )
    db_session.commit()

    response = client.post(f"/variants/{variant.id}/publish")
    assert response.status_code == 409


def test_job_store_persists_job_across_scheduler_restart():
    jobstore_a = SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs_test")
    scheduler_a = BackgroundScheduler(jobstores={"default": jobstore_a})
    scheduler_a.start(paused=True)
    scheduler_a.add_job(_dummy_job, "interval", seconds=30, id="test_persisted_job")

    try:
        jobstore_b = SQLAlchemyJobStore(engine=engine, tablename="apscheduler_jobs_test")
        scheduler_b = BackgroundScheduler(jobstores={"default": jobstore_b})
        scheduler_b.start(paused=True)
        try:
            job = scheduler_b.get_job("test_persisted_job")
            assert job is not None
            assert job.id == "test_persisted_job"
        finally:
            scheduler_b.shutdown(wait=False)
    finally:
        scheduler_a.remove_job("test_persisted_job")
        scheduler_a.shutdown(wait=False)


def test_publish_history_returns_attempt_with_context(client):
    variant_dict = next(v for v in _create_variants(client) if v["platform"] == "linkedin")
    client.patch(f"/variants/{variant_dict['id']}/approve")
    publish_response = client.post(f"/variants/{variant_dict['id']}/publish")
    assert publish_response.status_code == 200

    history_response = client.get("/publish-history")
    assert history_response.status_code == 200

    entries = history_response.json()
    match = next(e for e in entries if e["variant_id"] == variant_dict["id"])
    assert match["platform"] == "linkedin"
    assert match["status"] == "success"
    assert match["response_detail"] is not None


def test_schedule_rejects_past_time(client):
    variant_dict = next(v for v in _create_variants(client) if v["platform"] == "x")
    client.patch(f"/variants/{variant_dict['id']}/approve")

    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    response = client.post(
        f"/variants/{variant_dict['id']}/schedule",
        json={"scheduled_time": past_time.isoformat()},
    )
    assert response.status_code == 422
    assert "future" in response.json()["detail"]
