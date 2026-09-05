from datetime import datetime, timezone

import requests

# pyrefly: ignore [missing-import]
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.constraints import PROFILES, validate
from app.db import Base, engine, get_db
from app.generation import generate_variant
from app.ingestion import fetch_url_content
from app.models import Post, PublishAttempt, Slot, Variant
from app.publish_service import PublishPendingError, get_or_create_slot, publish_variant_now
from app.publishers.base import SocialPublisher
from app.publishers.registry import get_adapters
from app.schemas import (
    PostCreate,
    PostOut,
    PublishAttemptOut,
    RejectRequest,
    ScheduleRequest,
    VariantEdit,
    VariantOut,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Social Media Studio")


@app.post("/posts", response_model=PostOut, status_code=201)
def create_post(payload: PostCreate, db: Session = Depends(get_db)):
    if payload.url:
        try:
            content = fetch_url_content(payload.url)
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Could not fetch url: {exc}",
            ) from exc
        post = Post(source_type="url", source_url=payload.url, content=content)
    else:
        post = Post(source_type="markdown", source_url=None, content=payload.markdown)

    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@app.post("/posts/{post_id}/variants", response_model=list[VariantOut], status_code=201)
def create_variants(post_id: str, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"Post {post_id} not found")

    variants: list[Variant] = []
    for platform, profile in PROFILES.items():
        body, hashtags, source = generate_variant(profile, post.content)

        violations = validate(profile, body, hashtags)
        if violations:
            raise HTTPException(
                status_code=422,
                detail=f"{platform}: " + "; ".join(v.message for v in violations),
            )

        variant = Variant(
            post_id=post.id,
            platform=platform,
            body=body,
            hashtags=hashtags,
            status="draft",
            generation_source=source,
        )
        db.add(variant)
        variants.append(variant)

    db.commit()
    for variant in variants:
        db.refresh(variant)

    return variants


def _get_variant_or_404(db: Session, variant_id: str) -> Variant:
    variant = db.get(Variant, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")
    return variant


@app.patch("/variants/{variant_id}/approve", response_model=VariantOut)
def approve_variant(variant_id: str, db: Session = Depends(get_db)):
    variant = _get_variant_or_404(db, variant_id)
    variant.status = "approved"
    db.commit()
    db.refresh(variant)
    return variant


@app.patch("/variants/{variant_id}/reject", response_model=VariantOut)
def reject_variant(
    variant_id: str,
    payload: RejectRequest = RejectRequest(),
    db: Session = Depends(get_db),
):
    variant = _get_variant_or_404(db, variant_id)
    variant.status = "rejected"
    variant.rejection_reason = payload.rejection_reason
    db.commit()
    db.refresh(variant)
    return variant


@app.patch("/variants/{variant_id}", response_model=VariantOut)
def edit_variant(variant_id: str, payload: VariantEdit, db: Session = Depends(get_db)):
    variant = _get_variant_or_404(db, variant_id)

    new_body = payload.body if payload.body is not None else variant.body
    new_hashtags = payload.hashtags if payload.hashtags is not None else variant.hashtags

    profile = PROFILES[variant.platform]
    violations = validate(profile, new_body, new_hashtags)
    if violations:
        raise HTTPException(
            status_code=422,
            detail=f"{variant.platform}: " + "; ".join(v.message for v in violations),
        )

    variant.body = new_body
    variant.hashtags = new_hashtags
    variant.status = "draft"
    variant.rejection_reason = None

    db.commit()
    db.refresh(variant)
    return variant


@app.post("/variants/{variant_id}/schedule")
def schedule_variant(
    variant_id: str, payload: ScheduleRequest, db: Session = Depends(get_db)
):
    variant = _get_variant_or_404(db, variant_id)
    if variant.status != "approved":
        raise HTTPException(
            status_code=422,
            detail=(
                f"variant is in '{variant.status}' status; "
                "only 'approved' variants can be scheduled."
            ),
        )

    scheduled_time = payload.scheduled_time
    if scheduled_time.tzinfo is None:
        scheduled_time = scheduled_time.replace(tzinfo=timezone.utc)
    if scheduled_time <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=422,
            detail="scheduled_time must be in the future",
        )

    slot = get_or_create_slot(db, variant, scheduled_time)
    return {
        "variant_id": variant.id,
        "scheduled_time": slot.scheduled_time,
        "status": "scheduled",
    }


@app.post("/variants/{variant_id}/publish", response_model=VariantOut)
def publish_variant(
    variant_id: str,
    db: Session = Depends(get_db),
    adapters: dict[str, SocialPublisher] = Depends(get_adapters),
):
    variant = _get_variant_or_404(db, variant_id)
    if variant.status not in ("approved", "published"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"variant is in '{variant.status}' status; "
                "only 'approved' variants can be published."
            ),
        )

    slot = get_or_create_slot(db, variant)
    try:
        return publish_variant_now(db, variant, slot, adapters)
    except PublishPendingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/publish-history", response_model=list[PublishAttemptOut])
def publish_history(db: Session = Depends(get_db)):
    rows = (
        db.query(PublishAttempt, Slot, Variant)
        .join(Slot, PublishAttempt.slot_id == Slot.id)
        .join(Variant, Slot.variant_id == Variant.id)
        .order_by(PublishAttempt.attempted_at.desc())
        .all()
    )
    return [
        PublishAttemptOut(
            id=attempt.id,
            variant_id=variant.id,
            platform=variant.platform,
            slot_id=slot.id,
            idempotency_key=attempt.idempotency_key,
            status=attempt.status,
            response_detail=attempt.response_detail,
            attempted_at=attempt.attempted_at,
        )
        for attempt, slot, variant in rows
    ]
