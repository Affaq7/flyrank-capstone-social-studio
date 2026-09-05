from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PublishResult:
    success: bool
    external_id: str | None
    detail: str | None


class SocialPublisher(ABC):
    @abstractmethod
    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        """Publish `content` once. The caller (the /publish endpoint) is
        responsible for idempotency — deciding whether this call should
        happen at all based on `idempotency_key`'s history in
        publish_attempts. This method just performs the side effect and
        reports what happened; it never raises on a normal failure (bad
        webhook, network error) — that becomes success=False."""
        ...
