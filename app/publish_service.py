from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import PublishAttempt, Slot, Variant
from app.publishers.base import SocialPublisher


class PublishPendingError(Exception):
    """Raised when a slot's prior publish attempt is stuck in 'pending' —
    a previous run wrote the durable marker but crashed before it learned
    whether the adapter call actually succeeded. The outcome is unknown, so
    callers must NOT auto-retry; this must be resolved manually (check the
    real platform, then fix the row directly)."""

    def __init__(self, idempotency_key: str):
        self.idempotency_key = idempotency_key
        super().__init__(
            f"publish attempt for {idempotency_key} is 'pending' from a "
            "previous run with unknown outcome; refusing to auto-retry"
        )


def get_or_create_slot(
    db: Session, variant: Variant, scheduled_time: datetime | None = None
) -> Slot:
    """Reuse the variant's existing slot if it has one, updating its
    scheduled_time in place when a new one is given (rescheduling) —
    never create a second slot for the same variant. A fresh slot mints a
    new idempotency_key; reusing one is what makes repeated/rescheduled
    publish attempts for the same variant collapse onto the same key."""
    slot = db.query(Slot).filter(Slot.variant_id == variant.id).first()

    if slot is None:
        kwargs = {"variant_id": variant.id}
        if scheduled_time is not None:
            kwargs["scheduled_time"] = scheduled_time
        slot = Slot(**kwargs)
        db.add(slot)
        db.commit()
        db.refresh(slot)
    elif scheduled_time is not None:
        slot.scheduled_time = scheduled_time
        db.commit()
        db.refresh(slot)

    return slot


def publish_variant_now(
    db: Session,
    variant: Variant,
    slot: Slot,
    adapters: dict[str, SocialPublisher],
) -> Variant:
    """The one idempotent-publish code path — used by both the manual
    /publish endpoint and the scheduling worker's poll loop. Do not
    duplicate this logic anywhere else.

    A 'pending' row is written and committed BEFORE the adapter is called,
    so a crash during the network call still leaves a durable trace: on
    restart, a lingering 'pending' row raises PublishPendingError instead
    of silently calling the adapter again. This narrows (does not
    eliminate) the crash-during-publish window — see docs/DESIGN.md and
    README.md's known limitations for what's still not covered.
    """
    idempotency_key = f"{variant.id}:{slot.id}"
    attempt = (
        db.query(PublishAttempt)
        .filter(PublishAttempt.idempotency_key == idempotency_key)
        .first()
    )

    if attempt is not None and attempt.status == "success":
        return variant

    if attempt is not None and attempt.status == "pending":
        raise PublishPendingError(idempotency_key)

    if attempt is None:
        attempt = PublishAttempt(
            slot_id=slot.id,
            idempotency_key=idempotency_key,
            status="pending",
            response_detail=None,
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)

    result = adapters[variant.platform].publish(variant.body, idempotency_key)

    attempt.status = "success" if result.success else "failed"
    attempt.response_detail = result.detail
    attempt.attempted_at = datetime.now(timezone.utc)

    if result.success:
        variant.status = "published"

    db.commit()
    db.refresh(variant)
    return variant
