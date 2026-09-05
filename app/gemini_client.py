import os
import re

from google import genai

from app.constraints import ConstraintProfile

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=api_key)
    return _client


def _build_prompt(profile: ConstraintProfile, content: str) -> str:
    return (
        f"Write a {profile.platform} post based on the source content below.\n"
        f"Tone: {profile.tone_notes}\n"
        f"Hard limits: at most {profile.max_length} characters in the body, "
        f"at most {profile.max_hashtags} hashtags.\n"
        "Respond in EXACTLY this format, nothing else:\n"
        "BODY: <post text, no hashtags inside it>\n"
        "HASHTAGS: <comma-separated hashtags, each starting with #, or NONE>\n\n"
        f"Source content:\n{content}"
    )


def _parse_response(text: str) -> tuple[str, list[str]]:
    body_match = re.search(r"BODY:\s*(.*?)(?:\nHASHTAGS:|\Z)", text, re.DOTALL)
    hashtags_match = re.search(r"HASHTAGS:\s*(.*)", text, re.DOTALL)

    if not body_match:
        raise ValueError("Gemini response missing BODY section")

    body = body_match.group(1).strip()

    hashtags: list[str] = []
    if hashtags_match:
        raw = hashtags_match.group(1).strip()
        if raw and raw.upper() != "NONE":
            hashtags = [h.strip() for h in raw.split(",") if h.strip()]

    return body, hashtags


def generate(profile: ConstraintProfile, content: str) -> tuple[str, list[str]]:
    """Call Gemini for one platform's variant.

    Raises on any failure (missing/invalid key, API error, quota, unparsable
    response) — callers are responsible for falling back to a template.
    """
    client = _get_client()
    model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    response = client.models.generate_content(
        model=model_name,
        contents=_build_prompt(profile, content),
    )
    return _parse_response(response.text)
