import re

from app.constraints import ConstraintProfile

_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "were",
    "your", "about", "into", "their", "them", "then", "than", "also",
    "will", "which", "there", "these", "those", "been", "being",
}


def _extract_hashtags(content: str, max_hashtags: int) -> list[str]:
    words = re.findall(r"[A-Za-z]{4,}", content)
    seen: list[str] = []
    for word in words:
        lower = word.lower()
        if lower in _STOPWORDS or lower in seen:
            continue
        seen.append(lower)
        if len(seen) >= max_hashtags:
            break
    return [f"#{word}" for word in seen]


def render(profile: ConstraintProfile, content: str) -> tuple[str, list[str]]:
    """Deterministic fallback: always satisfies `profile` by construction."""
    hashtags = _extract_hashtags(content, profile.max_hashtags)

    body = " ".join(content.split())
    if len(body) > profile.max_length:
        body = body[: profile.max_length - 1].rstrip() + "…"

    return body, hashtags
