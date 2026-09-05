import uuid

from app.publishers.base import PublishResult, SocialPublisher


class _MockPublisher(SocialPublisher):
    """Shared mock behavior: records what would have been posted in an
    in-memory log instead of making a network call. The log is a debug
    aid only — publish_attempts is the durable record, for every adapter
    alike."""

    def __init__(self) -> None:
        self.log: list[dict] = []

    def publish(self, content: str, idempotency_key: str) -> PublishResult:
        external_id = str(uuid.uuid4())
        self.log.append(
            {
                "content": content,
                "idempotency_key": idempotency_key,
                "external_id": external_id,
            }
        )
        return PublishResult(
            success=True,
            external_id=external_id,
            detail=f"{type(self).__name__} recorded post",
        )


class MockXPublisher(_MockPublisher):
    pass


class MockLinkedInPublisher(_MockPublisher):
    pass
