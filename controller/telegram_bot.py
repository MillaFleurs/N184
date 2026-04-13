"""Telegram channel for N184 controller.

Receives messages from Telegram, publishes them to Honore via Redis.
Receives outbound messages from agents via callback, sends to Telegram.

Implements the Channel protocol (see channel.py).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode

if TYPE_CHECKING:
    from redis_bridge import RedisBridge

logger = logging.getLogger(__name__)

MAX_TELEGRAM_LENGTH = 4096


class TelegramChannel:
    """Telegram channel — bridges user messages to Honore via Redis."""

    prefix = "tg:"

    def __init__(
        self,
        token: str,
        redis_bridge: RedisBridge,
        assistant_name: str = "Honoré",
    ) -> None:
        self.token = token
        self.redis_bridge = redis_bridge
        self.assistant_name = assistant_name
        self._app: Application | None = None
        self._chat_map: dict[str, int] = {}

    def _build(self) -> Application:
        """Build the Telegram application."""
        self._app = (
            Application.builder()
            .token(self.token)
            .build()
        )

        self._app.add_handler(CommandHandler("chatid", self._cmd_chatid))
        self._app.add_handler(CommandHandler("ping", self._cmd_ping))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

        return self._app

    async def start(self) -> None:
        app = self._build()
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]
        logger.info("Telegram channel started (polling)")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()  # type: ignore[union-attr]
            await self._app.stop()
            await self._app.shutdown()

    # ── Handlers ──────────────────────────────────────────────────────

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming text messages."""
        if not update.effective_message or not update.effective_chat:
            return

        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        chat_jid = f"tg:{chat.id}"
        sender_name = user.full_name if user else "Unknown"

        # Register chat_id for outbound messages
        self._chat_map[chat_jid] = chat.id

        text = message.text or ""
        if not text.strip():
            return

        logger.info(
            "Telegram message from %s in %s: %s",
            sender_name,
            chat_jid,
            text[:80],
        )

        # Publish to Honore's input channel
        await self.redis_bridge.publish_to_agent(
            "honore",
            {
                "type": "message",
                "text": text,
                "sender": str(user.id) if user else "unknown",
                "sender_name": sender_name,
                "chat_jid": chat_jid,
                "timestamp": message.date.isoformat() if message.date else "",
            },
        )

    async def _cmd_chatid(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Return the chat ID for registration."""
        if update.effective_chat:
            await update.effective_chat.send_message(
                f"Chat JID: `tg:{update.effective_chat.id}`",
                parse_mode=ParseMode.MARKDOWN,
            )

    async def _cmd_ping(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Health check."""
        if update.effective_chat:
            await update.effective_chat.send_message("pong")

    async def _cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show N184 status."""
        if update.effective_chat:
            await update.effective_chat.send_message(
                f"N184 Controller active. Agent: {self.assistant_name}"
            )

    # ── Outbound Messages ─────────────────────────────────────────────

    async def send_message(
        self, chat_jid: str, text: str, sender: str | None = None
    ) -> None:
        """Send a message to a Telegram chat.

        Called by RedisBridge when an agent publishes to n184:messages:*.
        """
        if not self._app:
            logger.warning("Telegram app not initialized")
            return

        # Resolve chat_id from JID
        chat_id = self._chat_map.get(chat_jid)
        if chat_id is None and chat_jid.startswith("tg:"):
            try:
                chat_id = int(chat_jid[3:])
            except ValueError:
                logger.warning("Cannot parse chat_id from JID: %s", chat_jid)
                return

        if chat_id is None:
            logger.warning("Unknown chat JID: %s", chat_jid)
            return

        # Format with sender identity if provided
        if sender:
            text = f"*{sender}*: {text}"

        # Split long messages (Telegram 4096 char limit)
        chunks = _split_message(text, MAX_TELEGRAM_LENGTH)
        for chunk in chunks:
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception:
                # Fallback to plain text if Markdown fails
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                    )
                except Exception:
                    logger.exception("Failed to send to %s", chat_jid)


def _split_message(text: str, max_length: int) -> list[str]:
    """Split a message into chunks that fit Telegram's limit."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        # Try to split at a newline
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
