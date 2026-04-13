"""Redis bridge for N184 controller.

Handles two async loops:
1. Task watcher: BRPOP n184:tasks, routes to job_manager or Vautrin queue
2. Message relay: PSUBSCRIBE n184:messages:*, forwards to Telegram or agents
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Callable, Awaitable

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from job_manager import JobManager

logger = logging.getLogger(__name__)


class RedisBridge:
    """Bridges Redis pub/sub with the controller's Telegram bot and job manager."""

    def __init__(
        self,
        redis_url: str,
        on_agent_message: Callable[[str, str, str | None], Awaitable[None]],
        job_manager: JobManager | None = None,
    ) -> None:
        """
        Args:
            redis_url: Redis connection URL
            on_agent_message: Callback(chat_jid, text, sender) for outbound messages
            job_manager: JobManager instance for creating agent Jobs
        """
        self.redis_url = redis_url
        self.on_agent_message = on_agent_message
        self.job_manager = job_manager
        self._client: aioredis.Redis | None = None
        self._pubsub: aioredis.client.PubSub | None = None
        self._running = False

    async def connect(self) -> None:
        self._client = aioredis.from_url(self.redis_url, decode_responses=True)
        await self._client.ping()
        logger.info("Redis connected: %s", self.redis_url)

    async def publish_to_agent(self, agent_name: str, message: dict) -> None:
        """Send a message to an agent's input channel."""
        if self._client is None:
            raise RuntimeError("Redis not connected")
        channel = f"n184:input:{agent_name}"
        await self._client.publish(channel, json.dumps(message))
        logger.debug("Published to %s", channel)

    async def close_agent(self, agent_name: str) -> None:
        """Signal an agent to close."""
        if self._client:
            await self._client.publish(f"n184:close:{agent_name}", "close")

    async def set_job_input(self, job_name: str, container_input: dict) -> None:
        """Store ContainerInput for a k8s Job to pick up."""
        if self._client is None:
            raise RuntimeError("Redis not connected")
        key = f"n184:job-input:{job_name}"
        await self._client.set(key, json.dumps(container_input), ex=3600)
        logger.debug("Set job input: %s", key)

    async def get_session_id(self, agent_name: str) -> str | None:
        """Get persisted session ID for an agent."""
        if self._client:
            return await self._client.hget("n184:sessions", agent_name)
        return None

    async def set_session_id(self, agent_name: str, session_id: str) -> None:
        """Persist session ID for an agent."""
        if self._client:
            await self._client.hset("n184:sessions", agent_name, session_id)

    # ── Task Watcher ──────────────────────────────────────────────────

    async def _watch_tasks(self) -> None:
        """BRPOP n184:tasks and route task commands."""
        if self._client is None:
            return
        logger.info("Task watcher started (BRPOP n184:tasks)")

        while self._running:
            try:
                result = await self._client.brpop("n184:tasks", timeout=5)
                if result is None:
                    continue

                _, raw = result
                data = json.loads(raw)
                task_type = data.get("type", "")

                logger.info("Task received: type=%s", task_type)

                if task_type == "schedule_task":
                    await self._handle_schedule_task(data)
                elif task_type in ("pause_task", "resume_task", "cancel_task", "update_task"):
                    logger.info("Task management: %s %s", task_type, data.get("taskId"))
                    # TODO: implement task lifecycle management
                elif task_type == "register_group":
                    logger.info("Group registration: %s", data.get("name"))
                    # TODO: dynamic group registration
                else:
                    logger.warning("Unknown task type: %s", task_type)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in task watcher")
                await asyncio.sleep(1)

    async def _handle_schedule_task(self, data: dict) -> None:
        """Route a schedule_task command to the appropriate handler."""
        target_agent = data.get("targetAgent")
        prompt = data.get("prompt", "")
        schedule_type = data.get("schedule_type", "once")

        if not self.job_manager:
            logger.warning("No job_manager — cannot create agent Job")
            return

        if target_agent == "vautrin":
            # Push to Vautrin scaling queue (KEDA watches this)
            container_input = {
                "prompt": prompt,
                "groupFolder": f"n184-{target_agent}",
                "chatJid": data.get("targetJid", ""),
                "isMain": False,
                "isScheduledTask": True,
                "assistantName": "Vautrin",
            }
            if self._client:
                await self._client.lpush(
                    "n184:vautrin-queue", json.dumps(container_input)
                )
            logger.info("Pushed Vautrin task to scaling queue")
        elif target_agent in ("rastignac", "bianchon", "lousteau"):
            # Create on-demand k8s Job
            session_id = await self.get_session_id(target_agent)
            job_name = await self.job_manager.create_agent_job(
                agent_name=target_agent,
                prompt=prompt,
                session_id=session_id,
            )
            logger.info("Created %s Job: %s", target_agent, job_name)
        else:
            # Generic task — schedule via target JID
            target_jid = data.get("targetJid", "")
            logger.info(
                "Generic scheduled task for %s: %s",
                target_jid,
                prompt[:80],
            )

    # ── Message Relay ─────────────────────────────────────────────────

    async def _relay_messages(self) -> None:
        """Subscribe to n184:messages:* and forward to Telegram or agents."""
        if self._client is None:
            return

        self._pubsub = self._client.pubsub()
        await self._pubsub.psubscribe("n184:messages:*")
        logger.info("Message relay started (PSUBSCRIBE n184:messages:*)")

        while self._running:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message is None:
                    continue

                if message["type"] != "pmessage":
                    continue

                data = json.loads(message["data"])
                chat_jid = data.get("chatJid", "")
                text = data.get("text", "")
                sender = data.get("sender")

                if not text:
                    continue

                logger.info(
                    "Message from agent %s → %s (%d chars)",
                    data.get("agentName", "?"),
                    chat_jid,
                    len(text),
                )

                # Forward to Telegram (or other channel)
                await self.on_agent_message(chat_jid, text, sender)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in message relay")
                await asyncio.sleep(1)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> tuple[asyncio.Task, asyncio.Task]:
        """Start both watcher loops. Returns the task handles."""
        self._running = True
        task_watcher = asyncio.create_task(self._watch_tasks())
        message_relay = asyncio.create_task(self._relay_messages())
        return task_watcher, message_relay

    async def stop(self) -> None:
        self._running = False
        if self._pubsub:
            await self._pubsub.punsubscribe()
            await self._pubsub.close()
        if self._client:
            await self._client.close()
