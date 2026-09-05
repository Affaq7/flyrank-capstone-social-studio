from app import gemini_client, templates
from app.constraints import ConstraintProfile, validate


def generate_variant(profile: ConstraintProfile, content: str) -> tuple[str, list[str], str]:
    """Produce (body, hashtags, generation_source) for one platform.

    Tries Gemini first. Falls back to a deterministic template — which
    satisfies `profile` by construction — if Gemini errors (missing key,
    quota, API failure) or its output breaks the profile. Either way the
    result handed back here still gets re-validated by the caller before
    being stored: this function's job is only to pick the best candidate,
    not to be the last line of defense.
    """
    try:
        body, hashtags = gemini_client.generate(profile, content)
        if not validate(profile, body, hashtags):
            return body, hashtags, "gemini"
    except Exception:
        pass

    body, hashtags = templates.render(profile, content)
    return body, hashtags, "template_fallback"
