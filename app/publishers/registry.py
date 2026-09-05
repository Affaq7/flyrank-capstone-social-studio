from app.publishers.base import SocialPublisher
from app.publishers.discord import DiscordPublisher
from app.publishers.mocks import MockLinkedInPublisher, MockXPublisher

# The entire platform -> adapter mapping. The app never branches on platform
# name anywhere else — swapping an adapter is changing a value here, not
# touching business logic.
PLATFORM_ADAPTERS: dict[str, SocialPublisher] = {
    "discord": DiscordPublisher(),
    "x": MockXPublisher(),
    "linkedin": MockLinkedInPublisher(),
}


def get_adapters() -> dict[str, SocialPublisher]:
    return PLATFORM_ADAPTERS
