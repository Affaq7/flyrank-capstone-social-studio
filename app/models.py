import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Post(Base):
    __tablename__ = "posts"

    id = Column(String, primary_key=True, default=_uuid)
    source_type = Column(String, nullable=False)  # "url" | "markdown"
    source_url = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    variants = relationship("Variant", back_populates="post")


class Variant(Base):
    __tablename__ = "variants"

    id = Column(String, primary_key=True, default=_uuid)
    post_id = Column(String, ForeignKey("posts.id"), nullable=False)
    platform = Column(String, nullable=False)  # "discord" | "x" | "linkedin"
    body = Column(Text, nullable=False)
    hashtags = Column(ARRAY(String), nullable=False, default=list)
    status = Column(String, nullable=False, default="draft")
    # "gemini" | "template_fallback" — which code path produced `body`/`hashtags`
    generation_source = Column(String, nullable=False)
    rejection_reason = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    post = relationship("Post", back_populates="variants")
