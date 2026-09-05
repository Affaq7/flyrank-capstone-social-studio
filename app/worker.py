import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import Slot, Variant
from app.publish_service import PublishPendingError, publish_variant_now
from app.publishers.base import SocialPublisher
from app.publishers.registry import PLATFORM_ADAPTERS

logger = logging.getLogger("worker")


def poll_due_slots(db: Session, adapters: dict[str, SocialPublisher]) -> list[Variant]:
    """Find every currently-due, still-approved slot and publish it through
    the same idempotent code path the manual /publish endpoint uses. Every
    call re-derives "what's due right now" from the database, so it doesn't
    matter which tick (or which process) calls this — a restart's first
    poll just picks up wherever the backlog actually stands."""
    due = (
        db.query(Slot, Variant)
        .join(Variant, Slot.variant_id == Variant.id)
        .filter(Slot.scheduled_time <= datetime.now(timezone.utc))
        .filter(Variant.status == "approved")
        .all()
    )

    demo_delay = float(os.environ.get("WORKER_DEMO_DELAY_SECONDS", "0"))
    logger.info("Poll: found %d due slot(s)", len(due))

    touched: list[Variant] = []
    for slot, variant in due:
        logger.info("Publishing variant %s (%s)...", variant.id, variant.platform)
        try:
            updated = publish_variant_now(db, variant, slot, adapters)
        except PublishPendingError as exc:
            logger.warning(
                "Skipping slot %s: %s — needs manual reconciliation, not auto-retrying",
                slot.id,
                exc,
            )
            continue

        logger.info("  -> %s", "success" if updated.status == "published" else "failed, will retry")
        touched.append(updated)

        if demo_delay:
            time.sleep(demo_delay)

    return touched


def run_poll_once() -> None:
    """The real entrypoint APScheduler calls: opens its own session against
    the actual database and runs one poll cycle."""
    db = SessionLocal()
    try:
        poll_due_slots(db, PLATFORM_ADAPTERS)
    finally:
        db.close()
