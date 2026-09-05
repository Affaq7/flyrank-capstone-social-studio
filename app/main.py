import requests

# pyrefly: ignore [missing-import]
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.constraints import PROFILES, validate
from app.db import Base, engine, get_db
from app.generation import generate_variant
from app.ingestion import fetch_url_content
from app.models import Post, Variant
from app.schemas import PostCreate, PostOut, VariantOut

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
