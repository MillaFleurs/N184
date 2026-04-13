"""N184 Controller — Kubernetes-native orchestrator.

Replaces NanoClaw with a lightweight Python service that:
1. Runs a Telegram bot (receives user messages)
2. Bridges messages to Honore via Redis pub/sub
3. Watches Redis task queue and creates k8s Jobs for sub-agents
4. Relays agent messages back to Telegram
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from job_manager import JobManager
from redis_bridge import RedisBridge
from telegram_bot import TelegramBot

# ── Configuration ─────────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis.n184.svc.cluster.local:6379")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Honoré")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("n184-controller")


async def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set — exiting")
        sys.exit(1)

    logger.info("N184 Controller starting...")
    logger.info("  Redis:     %s", REDIS_URL)
    logger.info("  Assistant: %s", ASSISTANT_NAME)

    # ── Initialize components ─────────────────────────────────────────

    # 1. Job manager (k8s client)
    job_manager = JobManager(redis_bridge=None)  # type: ignore[arg-type]
    job_manager.initialize()

    # 2. Redis bridge (needs telegram send_message callback — set after bot init)
    redis_bridge = RedisBridge(
        redis_url=REDIS_URL,
        on_agent_message=lambda jid, text, sender: _noop(jid, text, sender),
        job_manager=job_manager,
    )
    await redis_bridge.connect()

    # Wire job_manager to redis_bridge
    job_manager.redis_bridge = redis_bridge

    # 3. Telegram bot
    telegram_bot = TelegramBot(
        token=TELEGRAM_BOT_TOKEN,
        redis_bridge=redis_bridge,
        assistant_name=ASSISTANT_NAME,
    )
    app = telegram_bot.build()

    # Wire outbound message callback
    redis_bridge.on_agent_message = telegram_bot.send_message

    # ── Start everything ──────────────────────────────────────────────

    # Start Redis watcher loops
    task_watcher, message_relay = await redis_bridge.start()
    logger.info("Redis bridge started (task watcher + message relay)")

    # Start Telegram bot (polling mode)
    logger.info("Starting Telegram bot (polling)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)  # type: ignore[union-attr]

    logger.info("N184 Controller running. Press Ctrl+C to stop.")

    # ── Wait for shutdown ─────────────────────────────────────────────

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    # ── Cleanup ───────────────────────────────────────────────────────

    logger.info("Shutting down...")
    await app.updater.stop()  # type: ignore[union-attr]
    await app.stop()
    await app.shutdown()
    task_watcher.cancel()
    message_relay.cancel()
    await redis_bridge.stop()
    logger.info("N184 Controller stopped.")


async def _noop(jid: str, text: str, sender: str | None) -> None:
    """Placeholder callback before Telegram bot is wired."""
    pass


if __name__ == "__main__":
    asyncio.run(main())
