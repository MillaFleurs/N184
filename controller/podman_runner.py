"""Podman dispatch backend for the N184 controller.

Drop-in replacement for the k8s ``JobManager`` (``job_manager.py``) used when
N184 runs via podman-compose instead of Kubernetes. Same interface the
``RedisBridge`` depends on — ``initialize()`` + ``create_agent_job(...)`` — but
instead of creating a k8s Job it does ``podman run`` of an ephemeral sub-agent
container attached to the compose network (``n184net``).

It also provides ``vautrin_scaler()``, a coroutine that replaces KEDA: it
watches the Redis ``n184:vautrin-queue`` and spawns Vautrin worker containers
(which BRPOP the queue) up to a cap, reaping finished ones.

The controller runs on the HOST, so it talks to podman directly (the proven
path on macOS) — no podman socket mounted into a container. API keys come from
the controller's own environment (the host .env), passed to each sub-agent via
``-e``; nothing is baked into an image.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from providers import Resolved, get_registry

logger = logging.getLogger(__name__)

AGENT_IMAGE = os.environ.get("N184_AGENT_IMAGE", "localhost/n184-agent:latest")
NETWORK = os.environ.get("N184_PODMAN_NETWORK", "n184net")
# Sub-agents reach Redis/ChromaDB by compose service name on the shared network.
AGENT_REDIS_URL = os.environ.get("N184_AGENT_REDIS_URL", "redis://redis:6379")

# Repo root = parent of this file's directory (controller/..).
REPO_ROOT = Path(__file__).resolve().parent.parent

# Shared Memory Palace on the host (same dir Honoré mounts via compose), so
# Lousteau's lessons and findings are visible across all agents and survive a
# podman reset. Overridable for non-default layouts.
PALACE_DIR = os.environ.get("N184_PALACE_DIR", str(REPO_ROOT / "data" / "palace"))

# API-key env vars forwarded from the controller's environment into sub-agents.
# Only those actually set are passed (an empty ANTHROPIC_API_KEY would shadow
# the OAuth token — same rule as the k8s secret builder).
_API_KEY_ENVS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
)

# Budget-guard env (Phase 0) forwarded so sub-agents honour the same caps.
_BUDGET_ENVS = (
    "N184_DAILY_TOKEN_CAP",
    "N184_SCAN_TOKEN_CAP",
    "N184_DAILY_BUDGET_CAP_USD",
    "N184_SCAN_BUDGET_CAP_USD",
)

VAUTRIN_MAX_WORKERS = int(os.environ.get("N184_VAUTRIN_MAX_WORKERS", "6"))
VAUTRIN_POLL_SEC = int(os.environ.get("N184_VAUTRIN_POLL_SEC", "10"))


async def _run(*args: str) -> tuple[int, str]:
    """Run a podman command, returning (returncode, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        "podman", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


class PodmanJobManager:
    """Creates ephemeral sub-agent containers via ``podman run``."""

    def __init__(self, redis_bridge: Any) -> None:
        self.redis_bridge = redis_bridge

    def initialize(self) -> None:
        """Verify podman is reachable and the compose network exists."""
        rc = os.system("podman info >/dev/null 2>&1")
        if rc != 0:
            raise RuntimeError(
                "podman is not reachable from the controller host. "
                "Is the podman machine running? (`podman machine start`)"
            )
        # The compose network is created by `podman compose up`; create it if
        # the controller is started first.
        if os.system(f"podman network exists {NETWORK} >/dev/null 2>&1") != 0:
            os.system(f"podman network create {NETWORK} >/dev/null 2>&1")
        logger.info("PodmanJobManager ready (image=%s network=%s)", AGENT_IMAGE, NETWORK)

    def _common_env(
        self,
        agent_name: str,
        job_name: str,
        resolved: Resolved,
        resource_limits: dict[str, Any] | None,
        scan_id: str | None,
    ) -> list[str]:
        rl = resource_limits or {}
        env: dict[str, str] = {
            "IPC_BACKEND": "redis",
            "REDIS_URL": AGENT_REDIS_URL,
            "N184_AGENT_NAME": agent_name,
            "JOB_NAME": job_name,
            "CHROMADB_HOST": "chromadb",
            "CHROMADB_PORT": "8000",
            "N184_MAX_TURNS": str(rl.get("max_turns", 32)),
            "N184_QUERY_TIMEOUT_MS": str(rl.get("timeout_ms", 1_800_000)),
        }
        if rl.get("max_budget_usd"):
            env["N184_MAX_BUDGET_USD"] = str(rl["max_budget_usd"])
        if scan_id:
            env["N184_SCAN_ID"] = scan_id
        # Provider routing (non-secret).
        env.update(resolved.env_overrides())
        # Budget-guard caps + API keys, forwarded from the controller's env.
        for key in (*_BUDGET_ENVS, *_API_KEY_ENVS):
            val = os.environ.get(key)
            if val:
                env[key] = val

        args: list[str] = []
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        return args

    def _common_mounts(self, agent_name: str) -> list[str]:
        soul = REPO_ROOT / "souls" / f"claude-{agent_name}.md"
        providers = REPO_ROOT / "providers"
        refs = REPO_ROOT / "souls" / "refs"
        args: list[str] = []
        if soul.exists():
            args += ["-v", f"{soul}:/workspace/group/CLAUDE.md:ro"]
        if providers.is_dir():
            args += ["-v", f"{providers}:/etc/n184/providers:ro"]
        if refs.is_dir():
            args += ["-v", f"{refs}:/workspace/refs:ro"]
        # Shared Memory Palace (Lousteau writes, others read) — host dir.
        args += ["-v", f"{PALACE_DIR}:/home/node/.n184"]
        return args

    async def create_agent_job(
        self,
        agent_name: str,
        prompt: str,
        session_id: str | None = None,
        chat_jid: str = "",
        timeout_seconds: int = 3600,
        provider: str | None = None,
        model: str | None = None,
        context_mode: str = "group",
        scan_id: str | None = None,
        resource_limits: dict[str, Any] | None = None,
    ) -> str:
        """Spawn an ephemeral sub-agent container; returns the container name."""
        resolved: Resolved = get_registry().resolve(provider, model)
        job_name = f"{agent_name}-{int(time.time() * 1000)}"

        container_input = {
            "prompt": prompt,
            "sessionId": session_id,
            "groupFolder": f"n184-{agent_name}",
            "chatJid": chat_jid,
            "isMain": False,
            "isScheduledTask": True,
            "assistantName": agent_name.capitalize(),
            "contextMode": context_mode,
            "provider": resolved.provider.name,
            "model": resolved.model,
            "scan_id": scan_id,
            "resourceLimits": resource_limits,
        }
        # The claude-sdk entrypoint (k8s-entrypoint.sh) fetches this from Redis
        # by JOB_NAME; openai-entrypoint does likewise.
        await self.redis_bridge.set_job_input(job_name, container_input)

        args = [
            "run", "-d", "--rm",
            "--name", job_name,
            "--network", NETWORK,
            *self._common_env(agent_name, job_name, resolved, resource_limits, scan_id),
            *self._common_mounts(agent_name),
            "--entrypoint", "",  # clear image ENTRYPOINT; runtime_command is argv
            AGENT_IMAGE,
            *resolved.runtime_command,
        ]
        rc, out = await _run(*args)
        if rc != 0:
            logger.error("podman run failed for %s: %s", job_name, out)
            raise RuntimeError(f"podman run failed for {job_name}: {out}")
        logger.info(
            "Spawned %s (provider=%s model=%s container=%s)",
            agent_name, resolved.provider.name, resolved.model, job_name,
        )
        return job_name

    # ── Vautrin autoscaler (replaces KEDA) ─────────────────────────────

    async def _running_vautrin_count(self) -> int:
        rc, out = await _run(
            "ps", "--filter", "name=vautrin-", "--format", "{{.Names}}"
        )
        if rc != 0 or not out:
            return 0
        return len([ln for ln in out.splitlines() if ln.strip()])

    async def _spawn_vautrin_worker(self) -> None:
        """Spawn one Vautrin worker that BRPOPs the queue (own provider/model
        travels per task on the queue, so the worker env is generic)."""
        job_name = f"vautrin-{int(time.time() * 1000)}"
        resolved = get_registry().resolve(None, None)  # generic claude-sdk base
        args = [
            "run", "-d", "--rm",
            "--name", job_name,
            "--network", NETWORK,
            *self._common_env("vautrin", job_name, resolved, None, None),
            *self._common_mounts("vautrin"),
            "--entrypoint", "",
            AGENT_IMAGE,
            "node", "/app/dist/vautrin-entrypoint.js",
        ]
        rc, out = await _run(*args)
        if rc != 0:
            logger.error("Failed to spawn Vautrin worker: %s", out)
        else:
            logger.info("Spawned Vautrin worker %s", job_name)

    async def vautrin_scaler(self, redis_client: Any) -> None:
        """Watch n184:vautrin-queue and keep enough workers running.

        Replaces the KEDA ScaledJob: when the queue has items and we're under
        the worker cap, spawn more. Workers are --rm and exit when idle, so the
        running count naturally drains back to zero.
        """
        logger.info(
            "Vautrin scaler started (max=%d, poll=%ds)",
            VAUTRIN_MAX_WORKERS, VAUTRIN_POLL_SEC,
        )
        while True:
            try:
                depth = await redis_client.llen("n184:vautrin-queue")
                if depth > 0:
                    running = await self._running_vautrin_count()
                    want = min(depth, VAUTRIN_MAX_WORKERS) - running
                    for _ in range(max(0, want)):
                        await self._spawn_vautrin_worker()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Error in Vautrin scaler")
            await asyncio.sleep(VAUTRIN_POLL_SEC)
