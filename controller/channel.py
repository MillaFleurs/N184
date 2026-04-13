"""Channel abstraction for N184 controller.

All messaging channels (Telegram, Slack, Email) implement the Channel
protocol. The ChannelRouter dispatches outbound messages to the right
channel based on the chat_jid prefix (tg:, slack:, email:).
"""

from __future__ import annotations

import logging
from typing import Protocol, Awaitable

logger = logging.getLogger(__name__)


class Channel(Protocol):
    """Protocol that all messaging channels must implement."""

    prefix: str  # JID prefix (e.g., "tg:", "slack:", "email:")

    async def start(self) -> None:
        """Start the channel (connect, authenticate, begin polling)."""
        ...

    async def stop(self) -> None:
        """Stop the channel gracefully."""
        ...

    async def send_message(
        self, chat_jid: str, text: str, sender: str | None = None
    ) -> None:
        """Send a message to a chat/channel/inbox."""
        ...


class ChannelRouter:
    """Routes outbound messages to the appropriate channel by JID prefix."""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}

    def register(self, channel: Channel) -> None:
        """Register a channel. Its prefix is used for routing."""
        self._channels[channel.prefix] = channel
        logger.info("Channel registered: %s", channel.prefix)

    async def send_message(
        self, chat_jid: str, text: str, sender: str | None = None
    ) -> None:
        """Route a message to the channel that owns the chat_jid."""
        for prefix, channel in self._channels.items():
            if chat_jid.startswith(prefix):
                await channel.send_message(chat_jid, text, sender)
                return
        logger.warning("No channel registered for JID: %s", chat_jid)

    async def start_all(self) -> None:
        """Start all registered channels."""
        for channel in self._channels.values():
            await channel.start()

    async def stop_all(self) -> None:
        """Stop all registered channels."""
        for channel in self._channels.values():
            await channel.stop()
