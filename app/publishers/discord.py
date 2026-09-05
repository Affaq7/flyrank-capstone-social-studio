import os

import httpx

from app.publishers.base import PublishResult, SocialPublisher


class DiscordPublisher(SocialPublisher):
    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
        if not webhook_url:
            return PublishResult(
                success=False,
                external_id=None,
                detail="DISCORD_WEBHOOK_URL is not set",
            )

        try:
            # wait=true makes Discord return the created message (with its
            # real id) instead of a bare 204 — otherwise there's nothing to
            # link publish_attempts back to.
            response = httpx.post(
                webhook_url,
                params={"wait": "true"},
                json={"content": content},
                timeout=10,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return PublishResult(success=False, external_id=None, detail=str(exc))

        message = response.json()
        return PublishResult(
            success=True,
            external_id=message.get("id"),
            detail="Discord message created",
        )
