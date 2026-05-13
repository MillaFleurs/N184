"""Provider registry loader for the N184 controller.

Reads providers/registry.yaml (built-in defaults) and providers/registry.local.yaml
(deployment-specific overrides, gitignored) and exposes a Resolver that
job_manager.py uses to translate (provider, model) into the env vars and
runtime command needed to launch a pod.

Crucially, this module touches NO API keys. It only reads env-var *names*
from the registry; the actual values are mounted into the pod from the
n184-api-keys k8s Secret at run time.

Model strings are passed through as opaque values — there is no model
allowlist, so new model releases work without code changes.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Where to look for registry files. Order matters: later wins on conflicts.
# In a k8s pod, both files are mounted via the n184-providers ConfigMap.
DEFAULT_REGISTRY_PATHS = [
    Path("/etc/n184/providers/registry.yaml"),       # ConfigMap mount (k8s)
    Path("/etc/n184/providers/registry.local.yaml"), # ConfigMap mount (k8s, optional)
    Path(__file__).resolve().parent.parent / "providers" / "registry.yaml",
    Path(__file__).resolve().parent.parent / "providers" / "registry.local.yaml",
]

# Allowed runtime identifiers. The job_manager uses these to pick the
# container command. Adding a new runtime here without also wiring up
# the entrypoint will fail loudly at dispatch time, which is what we want.
KNOWN_RUNTIMES = {"claude-sdk", "openai-sdk"}
KNOWN_TYPES = {"anthropic", "openai", "openai-compat"}


@dataclass(frozen=True)
class Provider:
    name: str
    type: str
    base_url: str
    api_key_env: str
    default_model: str
    runtime: str
    notes: str = ""

    def validate(self) -> None:
        if self.type not in KNOWN_TYPES:
            raise ValueError(
                f"Provider {self.name!r}: unknown type {self.type!r} "
                f"(expected one of {sorted(KNOWN_TYPES)})"
            )
        if self.runtime not in KNOWN_RUNTIMES:
            raise ValueError(
                f"Provider {self.name!r}: unknown runtime {self.runtime!r} "
                f"(expected one of {sorted(KNOWN_RUNTIMES)})"
            )
        if not self.base_url:
            raise ValueError(f"Provider {self.name!r}: base_url is required")
        if self.type == "anthropic" and self.runtime != "claude-sdk":
            raise ValueError(
                f"Provider {self.name!r}: type=anthropic requires runtime=claude-sdk"
            )


@dataclass(frozen=True)
class Resolved:
    """The result of resolving (provider, model) — what job_manager needs."""

    provider: Provider
    model: str

    @property
    def runtime_command(self) -> list[str]:
        """The container command for this runtime."""
        if self.provider.runtime == "claude-sdk":
            return ["/app/k8s-entrypoint.sh"]
        if self.provider.runtime == "openai-sdk":
            return ["node", "/app/dist/openai-entrypoint.js"]
        # validate() guarantees this is unreachable
        raise RuntimeError(f"unhandled runtime {self.provider.runtime!r}")

    def env_overrides(self) -> dict[str, str]:
        """Env vars the pod needs in addition to the standard set.

        These tell the runtime which provider+model to use and where to
        send requests. The actual API key is still pulled from the
        n184-api-keys Secret by job_manager — this dict only contains
        non-secret routing info.
        """
        env: dict[str, str] = {
            "N184_PROVIDER": self.provider.name,
            "N184_MODEL": self.model,
            "N184_PROVIDER_TYPE": self.provider.type,
            "N184_PROVIDER_BASE_URL": self.provider.base_url,
            "N184_PROVIDER_API_KEY_ENV": self.provider.api_key_env,
        }
        # The Anthropic SDK reads ANTHROPIC_BASE_URL when present. Setting
        # it lets us point claude-sdk runtime at proxies (LiteLLM, etc.)
        # without code changes.
        if self.provider.type == "anthropic":
            env["ANTHROPIC_BASE_URL"] = self.provider.base_url
        return env


class Registry:
    """In-memory provider registry, loaded from one or more YAML files."""

    def __init__(self, providers: dict[str, Provider]) -> None:
        self._providers = providers

    @classmethod
    def load(cls, paths: list[Path] | None = None) -> "Registry":
        paths = paths if paths is not None else DEFAULT_REGISTRY_PATHS
        merged: dict[str, dict[str, Any]] = {}
        loaded_any = False

        for path in paths:
            if not path.exists():
                continue
            loaded_any = True
            with path.open("r") as f:
                doc = yaml.safe_load(f) or {}
            for name, entry in (doc.get("providers") or {}).items():
                # Later files override earlier ones key-by-key.
                merged[name] = {**merged.get(name, {}), **entry}
            logger.info("Loaded provider registry from %s", path)

        if not loaded_any:
            raise FileNotFoundError(
                f"No provider registry found. Looked in: {[str(p) for p in paths]}"
            )

        providers: dict[str, Provider] = {}
        for name, entry in merged.items():
            provider = Provider(
                name=name,
                type=entry.get("type", ""),
                base_url=entry.get("base_url", ""),
                api_key_env=entry.get("api_key_env", ""),
                default_model=entry.get("default_model", ""),
                runtime=entry.get("runtime", ""),
                notes=entry.get("notes", ""),
            )
            provider.validate()
            providers[name] = provider

        if not providers:
            raise ValueError("Provider registry is empty")

        return cls(providers)

    def names(self) -> list[str]:
        return sorted(self._providers.keys())

    def get(self, name: str) -> Provider:
        if name not in self._providers:
            raise KeyError(
                f"Unknown provider {name!r}. Registered: {self.names()}. "
                f"Add it to providers/registry.local.yaml to enable."
            )
        return self._providers[name]

    def resolve(self, provider: str | None, model: str | None) -> Resolved:
        """Look up a provider and pick a model.

        - If provider is None, default to 'anthropic' (preserves legacy behavior).
        - If model is None, use the provider's default_model.
        - The model string is otherwise passed through opaquely — no allowlist.
        """
        provider_name = provider or "anthropic"
        p = self.get(provider_name)
        chosen_model = model or p.default_model
        if not chosen_model:
            raise ValueError(
                f"Provider {provider_name!r} has no default_model; "
                "caller must specify one explicitly."
            )
        return Resolved(provider=p, model=chosen_model)


# Module-level singleton. job_manager imports this directly; tests can
# rebuild it by calling Registry.load() with a custom path list.
_singleton: Registry | None = None


def get_registry() -> Registry:
    global _singleton
    if _singleton is None:
        # Allow override via env var for unusual deployments.
        override = os.environ.get("N184_PROVIDER_REGISTRY_PATHS")
        if override:
            paths = [Path(p) for p in override.split(":") if p]
            _singleton = Registry.load(paths)
        else:
            _singleton = Registry.load()
    return _singleton


def reset_registry_for_tests() -> None:
    global _singleton
    _singleton = None
