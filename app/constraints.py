from dataclasses import dataclass


@dataclass(frozen=True)
class ConstraintProfile:
    platform: str
    max_length: int
    max_hashtags: int
    max_media: int
    min_media: int
    tone_notes: str


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


DISCORD_PROFILE = ConstraintProfile(
    platform="discord",
    max_length=2000,
    max_hashtags=5,
    max_media=10,
    min_media=0,
    tone_notes="Casual, community-oriented; can reference channels/roles",
)

X_PROFILE = ConstraintProfile(
    platform="x",
    max_length=280,
    max_hashtags=3,
    max_media=4,
    min_media=0,
    tone_notes="Conversational, concise, can reference threads/replies",
)

LINKEDIN_PROFILE = ConstraintProfile(
    platform="linkedin",
    max_length=3000,
    max_hashtags=5,
    max_media=9,
    min_media=0,
    tone_notes="Professional, first-person insight; thought-leadership framing",
)

PROFILES: dict[str, ConstraintProfile] = {
    "discord": DISCORD_PROFILE,
    "x": X_PROFILE,
    "linkedin": LINKEDIN_PROFILE,
}


def validate(profile: ConstraintProfile, body: str, hashtags: list[str]) -> list[Violation]:
    violations: list[Violation] = []

    if len(body) > profile.max_length:
        violations.append(
            Violation(
                rule="max_length",
                message=f"max_length: {len(body)} > {profile.max_length}",
            )
        )

    if len(hashtags) > profile.max_hashtags:
        violations.append(
            Violation(
                rule="max_hashtags",
                message=f"max_hashtags: {len(hashtags)} > {profile.max_hashtags}",
            )
        )

    return violations
