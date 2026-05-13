# N184 Provider Registry

This directory declares which AI providers Honoré can dispatch sub-agents
against. It answers questions like:

- "What does it mean when Honoré says `provider=deepseek`?"
- "How do I let Honoré dispatch agents to my local Ollama?"
- "How do I keep my API keys out of the repo?"

## Files

| File | Source of truth | Committed? |
| --- | --- | --- |
| `registry.yaml` | Built-in defaults: `anthropic`, `openai`, `deepseek` | Yes |
| `registry.local.yaml` | Your deployment-specific additions/overrides | **No** (`.gitignore`d) |
| `registry.local.yaml.example` | Template you copy from | Yes |

The loader reads `registry.yaml` first, then deep-merges `registry.local.yaml`
on top. Local entries win.

## What lives here vs. where the keys live

This file declares only the *shape* of a provider:

- `type` — wire protocol (`anthropic`, `openai`, `openai-compat`)
- `base_url` — HTTP endpoint
- `api_key_env` — **name** of the env var holding the key (e.g. `OPENAI_API_KEY`)
- `default_model`, `runtime`, `notes`

The actual API key never appears in this directory. It lives in:

- Local dev: your `.env` file (gitignored — see `.env.example`)
- k8s: the `n184-api-keys` Secret (`k8s/base/api-secrets.yaml` is just a template)

This separation is deliberate: somebody copying the repo gets the *list* of
supported providers but cannot accidentally inherit your credentials, and
you can edit the registry safely without worrying about a stray paste.

## Adding a provider

```bash
cp providers/registry.local.yaml.example providers/registry.local.yaml
$EDITOR providers/registry.local.yaml
```

Add the key to your `.env` (or the k8s Secret) under the `api_key_env`
name you chose, and you're done — Honoré will see the new provider on
its next invocation of the `list_providers` MCP tool.

For k8s deployments, the registry is mounted into pods via the
`n184-providers` ConfigMap (`k8s/base/providers-configmap.yaml`). After
editing `registry.local.yaml`, re-run `kubectl apply -k k8s/base` to
refresh the ConfigMap.

## Future-proofing against new model releases

Model strings (`claude-opus-9`, `gpt-5.5`, whatever) are passed through
as opaque values. The registry only validates that the *provider* exists —
not that the specific model name is on a hardcoded list. When a new model
ships, Honoré can dispatch to it the day it's announced.

## Runtimes

`runtime` selects which agent-runner entrypoint executes the work:

- `claude-sdk` → `agent-runner/dist/index.js` (uses
  `@anthropic-ai/claude-agent-sdk`). Required for `type: anthropic`.
- `openai-sdk` → `agent-runner/dist/openai-entrypoint.js` (uses the
  OpenAI Node SDK with custom `baseURL`). Used for `type: openai` and
  `type: openai-compat` (DeepSeek, Ollama, LiteLLM, ...).
