"""Kubernetes Job manager for N184 agent dispatch.

Creates and monitors k8s Jobs for Rastignac, Bianchon, and other on-demand agents.
Vautrin Jobs are handled by KEDA ScaledJob (not created here).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

# Agent → ConfigMap name mapping
SOUL_CONFIGMAPS = {
    "honore": "n184-soul-honore",
    "rastignac": "n184-soul-rastignac",
    "vautrin": "n184-soul-vautrin",
    "bianchon": "n184-soul-bianchon",
    "lousteau": "n184-soul-lousteau",
}

NAMESPACE = "n184"
AGENT_IMAGE = "n184-agent:latest"
SECRET_NAME = "n184-api-keys"
PALACE_PVC = "n184-palace-pvc"


def _api_key_envs() -> list[client.V1EnvVar]:
    """Build env var list for all API keys from k8s Secret."""
    keys = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
    ]
    envs = []
    for key in keys:
        envs.append(
            client.V1EnvVar(
                name=key,
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name=SECRET_NAME,
                        key=key,
                        optional=key != "ANTHROPIC_API_KEY",
                    )
                ),
            )
        )
    # TODO: Add support for local LLM servers (Ollama, llama.cpp, MLX)
    # When implemented, add env vars like:
    #   OLLAMA_BASE_URL, LLAMA_CPP_BASE_URL, MLX_BASE_URL
    # These would point to k8s Services running local model servers.
    return envs


class JobManager:
    """Creates and monitors k8s Jobs for N184 agents."""

    def __init__(self, redis_bridge: Any) -> None:
        """
        Args:
            redis_bridge: RedisBridge instance for setting job input
        """
        self.redis_bridge = redis_bridge
        self._batch_api: client.BatchV1Api | None = None
        self._core_api: client.CoreV1Api | None = None

    def initialize(self) -> None:
        """Load k8s config and create API clients."""
        try:
            config.load_incluster_config()
            logger.info("Loaded in-cluster k8s config")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("Loaded local kubeconfig")

        self._batch_api = client.BatchV1Api()
        self._core_api = client.CoreV1Api()

    @property
    def batch_api(self) -> client.BatchV1Api:
        if self._batch_api is None:
            raise RuntimeError("JobManager not initialized")
        return self._batch_api

    async def create_agent_job(
        self,
        agent_name: str,
        prompt: str,
        session_id: str | None = None,
        chat_jid: str = "",
        timeout_seconds: int = 3600,
    ) -> str:
        """Create a k8s Job for an agent.

        Writes ContainerInput to Redis, then creates the Job.
        The Job's k8s-entrypoint.sh fetches input from Redis.

        Returns the Job name.
        """
        timestamp = int(time.time())
        job_name = f"{agent_name}-{timestamp}"
        soul_configmap = SOUL_CONFIGMAPS.get(agent_name, f"n184-soul-{agent_name}")

        # Write ContainerInput to Redis for the Job to fetch
        container_input = {
            "prompt": prompt,
            "sessionId": session_id,
            "groupFolder": f"n184-{agent_name}",
            "chatJid": chat_jid,
            "isMain": False,
            "isScheduledTask": True,
            "assistantName": agent_name.capitalize(),
        }
        await self.redis_bridge.set_job_input(job_name, container_input)

        # Build the Job spec
        job = client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=client.V1ObjectMeta(
                name=job_name,
                namespace=NAMESPACE,
                labels={
                    "app.kubernetes.io/name": agent_name,
                    "app.kubernetes.io/part-of": "n184",
                    "n184.io/agent": agent_name,
                },
            ),
            spec=client.V1JobSpec(
                backoff_limit=0,
                active_deadline_seconds=timeout_seconds,
                ttl_seconds_after_finished=300,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels={
                            "app.kubernetes.io/name": agent_name,
                            "app.kubernetes.io/part-of": "n184",
                        },
                    ),
                    spec=client.V1PodSpec(
                        node_selector={"n184.io/palace-node": "true"},
                        restart_policy="Never",
                        containers=[
                            client.V1Container(
                                name=agent_name,
                                image=AGENT_IMAGE,
                                image_pull_policy="IfNotPresent",
                                command=["/app/k8s-entrypoint.sh"],
                                env=[
                                    client.V1EnvVar(
                                        name="IPC_BACKEND", value="redis"
                                    ),
                                    client.V1EnvVar(
                                        name="REDIS_URL",
                                        value="redis://redis.n184.svc.cluster.local:6379",
                                    ),
                                    client.V1EnvVar(
                                        name="N184_AGENT_NAME",
                                        value=agent_name,
                                    ),
                                    client.V1EnvVar(
                                        name="JOB_NAME", value=job_name
                                    ),
                                    client.V1EnvVar(
                                        name="CHROMADB_HOST",
                                        value="chromadb.n184.svc.cluster.local",
                                    ),
                                    client.V1EnvVar(
                                        name="CHROMADB_PORT",
                                        value="8000",
                                    ),
                                    # API keys for multi-model swarm
                                    *_api_key_envs(),
                                ],
                                volume_mounts=[
                                    client.V1VolumeMount(
                                        name="soul",
                                        mount_path="/workspace/group/CLAUDE.md",
                                        sub_path="CLAUDE.md",
                                    ),
                                    client.V1VolumeMount(
                                        name="palace",
                                        mount_path="/home/node/.n184",
                                    ),
                                ],
                                resources=client.V1ResourceRequirements(
                                    requests={"memory": "512Mi", "cpu": "250m"},
                                    limits={"memory": "2Gi", "cpu": "2"},
                                ),
                            )
                        ],
                        volumes=[
                            client.V1Volume(
                                name="soul",
                                config_map=client.V1ConfigMapVolumeSource(
                                    name=soul_configmap,
                                ),
                            ),
                            client.V1Volume(
                                name="palace",
                                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                    claim_name=PALACE_PVC,
                                ),
                            ),
                        ],
                    ),
                ),
            ),
        )

        try:
            self.batch_api.create_namespaced_job(namespace=NAMESPACE, body=job)
            logger.info("Created Job %s/%s", NAMESPACE, job_name)
        except ApiException as e:
            logger.error("Failed to create Job %s: %s", job_name, e.reason)
            raise

        return job_name

    def list_agent_jobs(self, agent_name: str | None = None) -> list[dict]:
        """List active Jobs, optionally filtered by agent."""
        label_selector = "app.kubernetes.io/part-of=n184"
        if agent_name:
            label_selector += f",n184.io/agent={agent_name}"

        try:
            jobs = self.batch_api.list_namespaced_job(
                namespace=NAMESPACE, label_selector=label_selector
            )
            return [
                {
                    "name": j.metadata.name,
                    "agent": j.metadata.labels.get("n184.io/agent", "?"),
                    "active": j.status.active or 0,
                    "succeeded": j.status.succeeded or 0,
                    "failed": j.status.failed or 0,
                    "created": j.metadata.creation_timestamp.isoformat()
                    if j.metadata.creation_timestamp
                    else None,
                }
                for j in jobs.items
            ]
        except ApiException as e:
            logger.error("Failed to list Jobs: %s", e.reason)
            return []

    def delete_job(self, job_name: str) -> bool:
        """Delete a Job and its pods."""
        try:
            self.batch_api.delete_namespaced_job(
                name=job_name,
                namespace=NAMESPACE,
                body=client.V1DeleteOptions(propagation_policy="Background"),
            )
            logger.info("Deleted Job %s", job_name)
            return True
        except ApiException as e:
            logger.error("Failed to delete Job %s: %s", job_name, e.reason)
            return False
