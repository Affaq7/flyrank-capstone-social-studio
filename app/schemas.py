from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class PostCreate(BaseModel):
    url: str | None = None
    markdown: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self):
        if bool(self.url) == bool(self.markdown):
            raise ValueError("Provide exactly one of `url` or `markdown`")
        return self


class PostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_type: str
    source_url: str | None
    content: str


class VariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    post_id: str
    platform: str
    body: str
    hashtags: list[str]
    status: str
    generation_source: str
    rejection_reason: str | None


class VariantEdit(BaseModel):
    body: str | None = None
    hashtags: list[str] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if self.body is None and self.hashtags is None:
            raise ValueError("Provide at least one of `body` or `hashtags`")
        return self


class RejectRequest(BaseModel):
    rejection_reason: str | None = None


class ScheduleRequest(BaseModel):
    scheduled_time: datetime


class PublishAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    variant_id: str
    platform: str
    slot_id: str
    idempotency_key: str
    status: str
    response_detail: str | None
    attempted_at: datetime
