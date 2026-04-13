# N184

N184 uses multi-model AI consensus (Claude, DeepSeek, ChatGPT, Gemini) to find bugs and improve software stability. Multiple agents independently analyze code and vote on findings, reducing false positives with actionable PRs. Named after element 184's island of stability — because everyone deserves stable software.

**AI-powered bug discovery and software stability analysis**

---

> **Migration Notice:**
>
> I have a lot of experiments from when I was building N184 that need to be moved over now that we have a good, working, base version.
>
> N184 is actively migrating from NanoClaw/Podman to a Kubernetes-native architecture. The `main` branch may be unstable during this transition. For a stable release, use the tagged version:
>
> ```bash
> git clone https://github.com/MillaFleurs/N184.git
> cd N184
> git checkout v1.0.0
> ```
>
> v1.0.0 uses the original NanoClaw/Podman setup via `./init.sh`. See the [ROADMAP](ROADMAP.md) for what's changing.

---

## What is N184?

N184 is an AI-powered bug discovery platform that deploys multiple AI agents to analyze codebases for stability issues, logic errors, and security vulnerabilities.

Its power comes from a few unique features that allow it to find bugs and security vulnerabilities that are often missed:

- **Codebase Mapping**: An entire codebase is mapped and cross-referenced with documentation. Agents flag behavior that doesn't match docs, so you can fix code or documentation.
- **Git Archaeology**: Agents analyze git history to flag repeated errors. If a contributor makes the same mistake over and over, we catch it.
- **Multi-Model Consensus**: Multiple models (Claude, GPT, DeepSeek, Gemini) vote on findings. 2/3 consensus threshold filters false positives.
- **Devil's Advocate**: A systematic challenge process pushes for clear PRs with steps to reproduce.
- **Documentation Librarian**: Checks documentation to confirm where code differs from documented behavior.
- **Memory Palace**: An institutional knowledge store (SQLite + ChromaDB) that accumulates patterns, lessons learned, and culture profiles across analysis sessions.

N184 is not theoretical. It has found and fixed bugs in OpenBSD, Apple MLX, Apache httpd, Docker CLI, and ClickHouse. See [SCOREBOARD.md](SCOREBOARD.md) for the full track record.

### Key Features

- **Stability First**: Finds crashes, logic errors, memory bugs, and stability issues — not just security vulnerabilities
- **Multi-Model Swarm**: Claude, GPT, DeepSeek, Gemini, plus local models via Ollama/MLX (coming soon)
- **Structured Output**: JSON findings with suggested fixes, PoC code, and optional CVSS/CWE metadata
- **Memory Palace**: Seven-hall knowledge store that makes agents smarter over time
- **Kubernetes-Native**: Autoscaling agent swarms via k3s + KEDA (migration in progress)
- **Multi-Channel**: Telegram, Slack, and Email for human-in-the-loop communication
- **Maintainer-Friendly**: Frames findings as stability improvements, not security alarms. Security framing used only when genuinely warranted.

---

## Architecture

```
Human (HIL) <── Telegram / Slack / Email ──> Controller Pod
                                                  |
                                              Redis (pub/sub)
                                                  |
                                         Honoré (orchestrator)
                                       /   |    \     \        \
                                Rastignac  |  Bianchon Goriot  Lousteau
                                 (recon)   |   (docs)  (cons.) (memory)
                                           |
                                     Vautrin Swarm
                                    (KEDA autoscaled,
                                     multiple AI models)
                                             |
                                             v
                                      Memory Palace
                                     /              \
                               SQLite DB         ChromaDB Server
                            (relationships)    (7 halls of verbatim
                                                knowledge)
```

### Agent Naming Convention

Characters from Honoré de Balzac's *La Comédie Humaine*:

- **Honoré**: The orchestrator. Coordinates analysis, applies Devil's Advocate, presents findings.
- **Vautrin**: The vulnerability hunter. Runs in swarms with different AI models.
- **Rastignac**: Reconnaissance specialist. Maps codebases, identifies hotspots, builds code maps.
- **Bianchon**: Documentation librarian. Checks findings against docs, filters features from bugs.
- **Lousteau**: Memory Palace custodian. Maintains the seven halls, provides historical context, predicts maintainer responses. Cynical, world-weary, has seen every bug before.
- **Goriot**: Consensus validator. Patient, methodical, brings agents together.

Each character's traits map to their function. "Vautrin found it, but Goriot rejected it in consensus" is easier to parse than "Agent-001 found it, but Agent-004 rejected it."

---

## Requirements

### System Requirements

- **Kubernetes**: k3s (Linux) or k3d (macOS) for the new architecture
  - OR Podman 4.0+ for the v1.0.0 NanoClaw setup
- **Python**: 3.11 or higher
- **Node.js**: 20 or higher (for agent containers)
- **Git**: For cloning repositories to analyze
- **Docker**: Required on macOS for building container images
- **Helm**: For installing KEDA (auto-installed by setup script)

### API Keys (Required)

At minimum, you need an Anthropic key. Additional providers enable multi-model consensus:

- **Anthropic** (Claude): [console.anthropic.com](https://console.anthropic.com/) - **Required**
- **OpenAI** (GPT): [platform.openai.com/api-keys](https://platform.openai.com/api-keys) - Optional
- **DeepSeek**: [platform.deepseek.com](https://platform.deepseek.com/) - Optional
- **Google** (Gemini): [aistudio.google.com/apikey](https://aistudio.google.com/apikey) - Optional

### Messaging Channels (at least one required)

- **Telegram**: Get a bot token from [@BotFather](https://t.me/botfather)
- **Slack**: Create a Slack app with Socket Mode enabled
- **Email**: Any IMAP/SMTP server (Gmail, Fastmail, self-hosted)

---

## Quick Start

### Option A: Kubernetes (Recommended)

```bash
# 1. Clone
git clone https://github.com/MillaFleurs/N184.git
cd N184

# 2. Configure
cp .env.example .env
# Edit .env with your API keys and channel credentials

# 3. Deploy
bash k8s/setup.sh

# 4. Talk to Honoré via your configured channel (Telegram, Slack, or Email)
```

The setup script handles everything: installs k3s/k3d, installs KEDA, builds container images, creates secrets, and deploys all pods.

To enable secrets encryption at rest:

```bash
bash k8s/setup.sh --encrypt-secrets
```

### Option B: NanoClaw/Podman (v1.0.0)

```bash
git checkout v1.0.0
cp .env.example .env
# Edit .env
./init.sh
```

### Monitoring

```bash
kubectl get pods -n n184                    # Pod status
kubectl logs -f deploy/honore -n n184       # Honoré logs
kubectl logs -f deploy/controller -n n184   # Controller logs
kubectl get jobs -n n184                    # Sub-agent Jobs
kubectl get scaledjob -n n184              # Vautrin autoscaler status
```

---

## Configuration (.env)

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-...

# At least one messaging channel
TELEGRAM_BOT_TOKEN=123456:ABC...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
EMAIL_IMAP_HOST=imap.gmail.com
EMAIL_IMAP_USER=you@gmail.com
EMAIL_IMAP_PASS=app-password
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_POLL_INTERVAL=60

# Optional multi-model providers
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
GEMINI_API_KEY=AI...
```

---

## Project Structure

```
N184/
├── n184/                    # Memory Palace Python package
│   ├── palace.py            #   Unified API (N184MemoryPalace)
│   ├── sqlite_store.py      #   SQLite relational graph
│   ├── chromadb_store.py    #   ChromaDB vector store (7 halls)
│   └── config.py            #   Paths and constants
├── n184_palace_cli.py       # CLI wrapper for agents (n184-palace)
├── agent-runner/            # TypeScript agent runner (Claude Code SDK)
│   └── src/
│       ├── index.ts         #   Core query loop (Redis + file IPC)
│       ├── redis-ipc.ts     #   Redis pub/sub adapter
│       ├── ipc-mcp-stdio.ts #   MCP tools (send_message, schedule_task)
│       ├── honore-entrypoint.ts   # Persistent mode for Honoré
│       └── vautrin-entrypoint.ts  # Queue consumer for Vautrin
├── controller/              # Python control plane
│   ├── main.py              #   Asyncio entry point
│   ├── channel.py           #   Channel protocol + router
│   ├── telegram_bot.py      #   Telegram channel
│   ├── slack_channel.py     #   Slack channel (Socket Mode)
│   ├── email_channel.py     #   Email channel (IMAP poll + SMTP)
│   ├── redis_bridge.py      #   Task watcher + message relay
│   └── job_manager.py       #   k8s Job creation
├── container/               # Agent container image
│   ├── Dockerfile           #   node:22 + Python + chromadb + Claude Code
│   ├── entrypoint.sh        #   Standard stdin entry
│   ├── k8s-entrypoint.sh    #   k8s Job entry (fetches from Redis)
│   └── build.sh             #   Build + import to k3d
├── k8s/                     # Kubernetes manifests (Kustomize)
│   ├── base/                #   Namespace, RBAC, Redis, ChromaDB,
│   │                        #   Controller, Honoré, KEDA Vautrin
│   ├── overlays/local/      #   macOS (k3d) patches
│   ├── overlays/production/ #   Linux (k3s) patches
│   └── setup.sh             #   One-command deploy
├── souls/                   # Agent persona definitions
│   ├── claude-honore.md     #   Lead orchestrator
│   ├── claude-vautrin.md    #   Vulnerability hunter
│   ├── claude-rastignac.md  #   Reconnaissance specialist
│   ├── claude-bianchon.md   #   Documentation librarian
│   └── claude-lousteau.md   #   Memory Palace custodian
├── SCOREBOARD.md            # Verified bugs found by N184
├── ROADMAP.md               # Feature roadmap
├── FAQ.md                   # Frequently asked questions
├── OVERVIEW.md              # Detailed file-by-file overview
└── LICENSE                  # AGPL v3
```

---

## How It Works

1. Human gives Honoré a repository to analyze (via Telegram, Slack, or Email)
2. Honoré dispatches Rastignac to map the codebase
3. Lousteau searches the Memory Palace for prior analysis of this codebase — known patterns, past false positives, culture profile — and feeds this context to Honoré and Rastignac so the swarm doesn't repeat old mistakes
4. Rastignac produces a code map with prioritized files, git history patterns, and Lousteau's historical context
5. Honoré dispatches the Vautrin swarm — KEDA autoscales pods using different AI models
6. Bianchon checks findings against documentation (feature vs bug)
7. Lousteau cross-references every finding against the Memory Palace: has this pattern been seen before? In which codebases? What was the outcome? Was it a false positive last time? He annotates findings with genealogy and confidence adjustments.
8. Goriot validates consensus across models (2/3 threshold)
9. Honoré applies Devil's Advocate filtering and presents findings to the human
10. Lousteau takes the human's feedback — confirmed bugs, false positives, near-misses — and archives everything in the Memory Palace. He evolves detection patterns, updates culture profiles, records statistics, and links cross-codebase tunnels so the next analysis starts smarter than this one ended.

---

## Design Philosophy

**Stability over security theater.** N184's primary goal is making software more stable and correct. When we find a bug, we describe what breaks and how to fix it — not how an attacker might exploit it. Security vulnerabilities are a subset of bugs, and they get flagged clearly when warranted, but the default posture is "here's a bug, here's a fix."

**N184 is convergent evolution, not competition.** Glasswing proved the category exists. AISLE proved small models can outperform large ones with the right system design. N184 proves you don't need $100M to make software more stable.

The adding machine didn't eliminate accountants. LLMs won't eliminate engineers. They're force multipliers. N184 is the adding machine moment for bug detection.

---

## MyMilla

Back in October 2025, Dan got involved in the ARM AI Developer Hackathon. It was fun, and he experimented with a personal assistant called [MyMilla](https://github.com/MillaFleurs/MyMilla), named after his late cat. He learned a lot, and explored some Lisp-specific techniques like homoiconicity and how they applied to AI agents.

He then realized there was no shortage of AI agent projects that did what MyMilla did — better, and easier. Several patterns from MyMilla have been integrated into N184:

- **Memory DSL** (fact/desire/opinion/backlog) — ported to `n184/memory_dsl.py`, gives agents structured vocabulary for storing knowledge about the HIL and project context
- **Self-edit safety model** — ported to `n184/self_edit.py`, provides 5-layer guarded self-modification (disabled by default, intent matching, path whitelisting, user confirmation, atomic backups)

The MyMilla project is now archived. Rest well, Milla.

---

## Contributing

1. Submit PRs improving agent prompts, adding validation checks, or addressing open issues
2. Report false positives to help improve filtering
3. Add support for new LLM providers
4. Improve documentation or create tutorials
5. Share bug patterns you've discovered
6. Financial support if you can't contribute time

---

## Attributions

N184 builds on ideas from a number of academic and industry sources. This section will be expanded with proper citations.

<!-- TODO: Add academic paper citations. Areas to cover:
  - Ensemble methods for defect detection
  - LLM-based code analysis and vulnerability discovery
  - Memory palace / method of loci applied to AI systems
  - Multi-agent consensus and voting systems
  - Homoiconicity and code-as-data in AI agent architectures
  - Git history analysis for bug prediction
  - False positive reduction in static analysis
  - De-securitization and risk communication framing
-->

---

## Authors

N184 was created through the cowork of A.L. Figaro and Dan Anderson ([github.com/MillaFleurs](https://github.com/MillaFleurs))

## License

See [LICENSE](LICENSE). This software is distributed under the terms of the GNU Affero General Public License v. 3.0.
