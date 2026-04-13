"""N184 Controller — Kubernetes-native orchestrator.

Replaces NanoClaw with a lightweight Python service that:
1. Runs messaging channels (Telegram, Slack, Email)
2. Bridges messages to Honore via Redis pub/sub
3. Watches Redis task queue and creates k8s Jobs for sub-agents
4. Relays agent messages back to the originating channel
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from channel import ChannelRouter
from job_manager import JobManager
from redis_bridge import RedisBridge
from telegram_bot import TelegramChannel
from slack_channel import SlackChannel
from email_channel import EmailChannel

# ── Configuration ─────────────────────────────────────────────────────

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis.n184.svc.cluster.local:6379")
ASSISTANT_NAME = os.environ.get("ASSISTANT_NAME", "Honoré")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Channel credentials (all optional — only enabled channels start)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APP_TOKEN = os.environ.get("SLACK_APP_TOKEN", "")
EMAIL_IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "")
EMAIL_IMAP_USER = os.environ.get("EMAIL_IMAP_USER", "")
EMAIL_IMAP_PASS = os.environ.get("EMAIL_IMAP_PASS", "")
EMAIL_SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "587"))
EMAIL_POLL_INTERVAL = int(os.environ.get("EMAIL_POLL_INTERVAL", "60"))
EMAIL_FOLDER = os.environ.get("EMAIL_FOLDER", "INBOX")

# ── Logging ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("n184-controller")


async def main() -> None:
    logger.info("N184 Controller starting...")
    logger.info("  Redis:     %s", REDIS_URL)
    logger.info("  Assistant: %s", ASSISTANT_NAME)

    # ── Initialize components ─────────────────────────────────────────

    # 1. Job manager (k8s client)
    job_manager = JobManager(redis_bridge=None)  # type: ignore[arg-type]
    job_manager.initialize()

    # 2. Channel router
    router = ChannelRouter()

    # 3. Redis bridge — outbound messages go through the router
    redis_bridge = RedisBridge(
        redis_url=REDIS_URL,
        on_agent_message=router.send_message,
        job_manager=job_manager,
    )
    await redis_bridge.connect()
    job_manager.redis_bridge = redis_bridge

    # ── Register Channels ─────────────────────────────────────────────

    enabled_channels: list[str] = []

    # Telegram
    if TELEGRAM_BOT_TOKEN:
        telegram = TelegramChannel(
            token=TELEGRAM_BOT_TOKEN,
            redis_bridge=redis_bridge,
            assistant_name=ASSISTANT_NAME,
        )
        router.register(telegram)
        enabled_channels.append("Telegram")
    else:
        logger.info("Telegram: disabled (no TELEGRAM_BOT_TOKEN)")

    # Slack
    if SLACK_BOT_TOKEN and SLACK_APP_TOKEN:
        try:
            slack = SlackChannel(
                bot_token=SLACK_BOT_TOKEN,
                app_token=SLACK_APP_TOKEN,
                redis_bridge=redis_bridge,
                assistant_name=ASSISTANT_NAME,
            )
            router.register(slack)
            enabled_channels.append("Slack")
        except ImportError as e:
            logger.warning("Slack: disabled (%s)", e)
    else:
        logger.info("Slack: disabled (no SLACK_BOT_TOKEN/SLACK_APP_TOKEN)")

    # Email
    if EMAIL_IMAP_HOST and EMAIL_IMAP_USER and EMAIL_IMAP_PASS:
        smtp_host = EMAIL_SMTP_HOST or EMAIL_IMAP_HOST.replace("imap.", "smtp.")
        email_ch = EmailChannel(
            imap_host=EMAIL_IMAP_HOST,
            imap_user=EMAIL_IMAP_USER,
            imap_pass=EMAIL_IMAP_PASS,
            smtp_host=smtp_host,
            smtp_port=EMAIL_SMTP_PORT,
            poll_interval=EMAIL_POLL_INTERVAL,
            folder=EMAIL_FOLDER,
            redis_bridge=redis_bridge,
            assistant_name=ASSISTANT_NAME,
        )
        router.register(email_ch)
        enabled_channels.append(f"Email (poll: {EMAIL_POLL_INTERVAL}s)")
    else:
        logger.info("Email: disabled (no EMAIL_IMAP_HOST/USER/PASS)")

    if not enabled_channels:
        logger.error("No messaging channels configured — exiting")
        sys.exit(1)

    logger.info("Channels: %s", ", ".join(enabled_channels))

    # ── Start everything ──────────────────────────────────────────────

    # Start Redis watcher loops
    task_watcher, message_relay = await redis_bridge.start()
    logger.info("Redis bridge started (task watcher + message relay)")

    # Start all registered channels
    await router.start_all()
    logger.info("All channels started")

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
    await router.stop_all()
    task_watcher.cancel()
    message_relay.cancel()
    await redis_bridge.stop()
    logger.info("N184 Controller stopped.")


if __name__ == "__main__":
    asyncio.run(main())
