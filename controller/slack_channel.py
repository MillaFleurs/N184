"""Slack channel for N184 controller.

Connects to Slack via Socket Mode (no public URL needed).
Receives messages, publishes to Honore via Redis.
Sends outbound messages from agents to Slack channels.

Implements the Channel protocol (see channel.py).

Requires:
  SLACK_BOT_TOKEN — Bot User OAuth Token (xoxb-...)
  SLACK_APP_TOKEN — App-Level Token for Socket Mode (xapp-...)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False

if TYPE_CHECKING:
    from redis_bridge import RedisBridge

logger = logging.getLogger(__name__)


class SlackChannel:
    """Slack channel — bridges messages between Slack and Honore via Redis."""

    prefix = "slack:"

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        redis_bridge: RedisBridge,
        assistant_name: str = "Honoré",
    ) -> None:
        if not HAS_SLACK:
            raise ImportError(
                "slack_bolt is not installed. "
                "Install with: pip install slack-bolt"
            )
        self.bot_token = bot_token
        self.app_token = app_token
        self.redis_bridge = redis_bridge
        self.assistant_name = assistant_name
        self._app: AsyncApp | None = None
        self._handler: AsyncSocketModeHandler | None = None

    async def start(self) -> None:
        self._app = AsyncApp(token=self.bot_token)

        @self._app.event("message")
        async def handle_message(event: dict, say: object) -> None:
            text = event.get("text", "")
            if not text.strip():
                return

            channel_id = event.get("channel", "")
            user_id = event.get("user", "unknown")
            chat_jid = f"slack:{channel_id}"

            logger.info(
                "Slack message from %s in %s: %s",
                user_id,
                chat_jid,
                text[:80],
            )

            await self.redis_bridge.publish_to_agent(
                "honore",
                {
                    "type": "message",
                    "text": text,
                    "sender": user_id,
                    "sender_name": user_id,
                    "chat_jid": chat_jid,
                    "timestamp": event.get("ts", ""),
                },
            )

        self._handler = AsyncSocketModeHandler(self._app, self.app_token)
        await self._handler.start_async()
        logger.info("Slack channel started (Socket Mode)")

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()

    async def send_message(
        self, chat_jid: str, text: str, sender: str | None = None
    ) -> None:
        if not self._app:
            logger.warning("Slack app not initialized")
            return

        # Extract channel ID from JID (slack:C1234567890)
        channel_id = chat_jid.removeprefix("slack:")
        if not channel_id:
            logger.warning("Invalid Slack JID: %s", chat_jid)
            return

        if sender:
            text = f"*{sender}*: {text}"

        try:
            await self._app.client.chat_postMessage(
                channel=channel_id,
                text=text,
                mrkdwn=True,
            )
        except Exception:
            logger.exception("Failed to send to Slack %s", chat_jid)
