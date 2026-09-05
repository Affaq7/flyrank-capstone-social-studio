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
