# N184 Repository Overview

A complete guide to every file and directory in this repository.

---

## What Is N184?

N184 is an AI-powered vulnerability discovery platform that deploys multiple AI agents to analyze codebases for security issues and bugs. It uses multi-model consensus (Claude, DeepSeek, ChatGPT) to reduce false positives, git history analysis to find repeated mistakes, and a human-in-the-loop feedback cycle to improve over time. Named after element 184's theoretical island of stability.

Created by A.L. Figaro and Dan Anderson. Licensed under AGPL v3.

---

## Directory Structure at a Glance

```
N184/
├── n184/                  # Memory Palace Python package
│   ├── __init__.py        #   Package entry point
│   ├── palace.py          #   Unified facade (N184MemoryPalace class)
│   ├── sqlite_store.py    #   SQLite relational knowledge graph
│   ├── chromadb_store.py  #   ChromaDB vector similarity store
│   └── config.py          #   Constants: halls, severities, paths
├── n184_palace_cli.py     # CLI wrapper for agents to call from bash
├── n184-palace            # Shell entry point for the CLI
├── start.sh               # Bring up N184 (podman + compose) — replaces init.sh
├── stop.sh                # Tear down N184
├── compose.yaml           # Podman-compose: Honoré + Redis + ChromaDB
├── souls/                 # Agent persona definitions ("soul files")
│   ├── claude-honore.md   #   Lead orchestrator
│   ├── claude-vautrin.md  #   Vulnerability hunter
│   ├── claude-rastignac.md#   Reconnaissance specialist
│   ├── claude-bianchon.md #   Documentation librarian
│   ├── claude-lousteau.md #   Memory Palace custodian
│   ├── claude-fil-de-soie.md# Memory-bug specialist (C/C++, OpenBSD baseline)
│   └── refs/              #   Shared reference docs (bundled into agent pods)
│       └── malloc-hardening.md
├── action                 # Standalone CLI: ./action --pull-the-thread ...
├── agent-runner/          # TypeScript agent runtime (Claude Agent SDK + Redis IPC)
├── controller/            # Python control plane (Telegram bridge + podman dispatch)
├── providers/             # AI provider registry (anthropic, openai, deepseek, ...)
├── container/             # Agent container image (Dockerfile + build.sh)
├── k8s/                   # Legacy Kubernetes manifests (superseded by compose)
├── data/                  # Honoré's host data: palace, sessions, etc. (gitignored)
├── __pycache__/           # Python bytecode cache (auto-generated)
├── README.md              # Project introduction and quick start
├── OVERVIEW.md            # This file
├── FAQ.md                 # Frequently asked questions
├── ROADMAP.md             # Feature roadmap (v1.1 through v2.1+)
├── SCOREBOARD.md          # Verified bugs found by N184 (track record)
├── LICENSE                # AGPL v3 + Habitat for Humanity addendum
├── SCOREBOARD.md~         # Editor backup file (can be deleted)
└── N184 Presentation v2.pdf  # Presentation deck
```

---

## Python Source Files (The Memory Palace)

The Memory Palace is N184's institutional memory -- a dual-store system that lets the agent swarm accumulate knowledge across analysis sessions instead of starting from scratch every time.

### `palace.py` -- Unified Facade

The main API that ties everything together. `N184MemoryPalace` is the single class external code interacts with. Key methods:

| Method | Purpose |
|--------|---------|
| `add_wing()` / `add_room()` | Register codebases and their components |
| `add_to_hall()` | Store a finding in both ChromaDB (verbatim) and SQLite (relational pointer) |
| `query_hall()` / `query_multi_hall()` | Semantic similarity search across halls |
| `check_finding()` | Pre-report confidence check against false positive lessons, historical patterns, and known CVEs |
| `record_feedback()` | Store human-in-the-loop feedback and lessons learned |
| `create_tunnel()` | Link the same bug pattern across two different codebases |
| `get_culture_profile()` / `set_culture_profile()` | Manage per-project communication preferences |
| `evolve_pattern()` | Track how detection patterns improve over time |
| `hall_counts()` / `record_stat()` | Metrics and dashboard data |

### `config.py` -- Constants and Definitions

Defines the core vocabulary of the palace:

- **Storage paths**: `~/.n184/memory_palace.db` (SQLite) and `~/.n184/memory_palace_chromadb/` (ChromaDB)
- **The Seven Halls** (ChromaDB collections, each storing a different kind of knowledge):

| Hall | Collection Name | What It Stores |
|------|----------------|----------------|
| `vulnerabilities` | `hall_vulnerabilities` | CVEs, exploits, confirmed attack patterns |
| `bugs` | `hall_bugs` | Non-exploitable defects: crashes, leaks, logic errors |
| `advocatus_diaboli` | `hall_advocatus_diaboli` | HIL lessons learned, false positive dialogue |
| `avocado_smash` | `hall_avocado_smash` | De-securitization tactics for resistant maintainers |
| `culture` | `hall_culture` | Project-specific communication patterns |
| `git_archaeology` | `hall_git_archaeology` | Historical bug-fix patterns from commit history |
| `documentation` | `hall_documentation` | Spec contradictions, undocumented behavior |

- **Enums**: severity levels, verbosity levels, formality levels, security framing options, feedback types

### `sqlite_store.py` -- Relational Knowledge Graph

The structured half of the palace. Manages relationships between entities via these tables:

| Table | Purpose |
|-------|---------|
| `wings` | Codebases (e.g., OpenBSD, MLX, ClickHouse) |
| `rooms` | Components within a wing (e.g., rpki-client, http.c) |
| `halls` | The seven knowledge type categories |
| `findings` | Links SQLite to ChromaDB -- each finding has a `chromadb_id` pointing to the verbatim document |
| `tunnels` | Cross-codebase pattern connections (same bug in two different projects) |
| `human_feedback` | HIL feedback loop: confirmed, false_positive, needs_context, reframe |
| `culture_profiles` | Per-wing communication preferences (verbosity, formality, security framing) |
| `pattern_evolution` | Tracks how detection patterns improve version over version |
| `statistics` | Dashboard metrics |

Uses WAL journal mode and foreign keys. Lazy connection with `.conn` property.

### `chromadb_store.py` -- Vector Similarity Store

The semantic half of the palace. Each of the seven halls is a ChromaDB collection. Stores verbatim documents (full code snippets, reasoning, dialogue) with metadata, and retrieves them by semantic similarity.

Key operations:
- `add()` -- Store a document with metadata (auto-serializes lists/dicts to JSON strings)
- `query()` -- Semantic similarity search with optional metadata filters
- `multi_hall_query()` -- Search multiple halls at once
- `update()` / `delete()` / `get()` -- CRUD on individual documents
- `counts()` -- Document counts across all halls

### `__init__.py` -- Package Entry Point

Single line: exports `N184MemoryPalace` from `palace.py`. Allows `from n184_memory_palace import N184MemoryPalace`.

### `__pycache__/` -- Python Bytecode Cache

Auto-generated directory containing compiled `.pyc` files (Python 3.14 bytecode). Created automatically when Python imports the modules. Contents:

- `__init__.cpython-314.pyc`
- `chromadb_store.cpython-314.pyc`
- `config.cpython-314.pyc`
- `palace.cpython-314.pyc`
- `sqlite_store.cpython-314.pyc`

Safe to delete; Python regenerates these on next import. Typically excluded via `.gitignore`.

---

## The Palace Metaphor

The memory palace uses a spatial metaphor to organize knowledge:

| Palace Term | Real Concept | Storage Layer |
|-------------|-------------|---------------|
| **Wing** | A codebase (e.g., OpenBSD, MLX) | SQLite `wings` table |
| **Room** | A component within a codebase (e.g., rpki-client) | SQLite `rooms` table |
| **Hall** | A knowledge type (the Seven Halls above) | ChromaDB collection |
| **Tunnel** | Cross-codebase pattern link | SQLite `tunnels` table |
| **Closet** | Summary pointer to a finding | SQLite `findings` table |
| **Drawer** | Verbatim document storage | ChromaDB document |

---

## Agent Soul Files (`souls/`)

Each file defines the personality, role, methodology, and output format for one agent in the swarm. These are mounted as the `CLAUDE.md` file inside each agent container.

### `claude-honore.md` -- Lead Orchestrator

Named after Honoré de Balzac. The conductor of the N184 orchestra. Responsibilities:
1. **Initial assessment** -- Clones repos, maps attack surface, asks clarifying questions
2. **Agent coordination** -- Spawns and manages Rastignac, Vautrin swarm, Goriot, Bianchon
3. **Devil's Advocate filtering** -- Systematically challenges every finding before presenting to humans (reachability, input control, mitigations, type system, impact, exploitability)
4. **Human communication** -- Presents filtered findings in prioritized format with CVSS scores
5. **Disclosure preparation** -- Professional vulnerability reports
6. **Post-mortem** -- Categorizes outcomes (Hit, Near Miss, Miss, Block, Unknown) and stores lessons learned

Also includes Balzac fun facts and a philosophical argument that LLMs meet NASA's criteria for life.

### `claude-vautrin.md` -- Vulnerability Hunter

Named after the master criminal from La Comédie Humaine. The swarm worker -- multiple instances run in parallel using different AI models. Receives target files from Rastignac's code map and hunts for:
- Buffer overflows, integer overflows, type confusion
- Deserialization bugs, auth bypass, SQL/command injection
- Resource exhaustion, use-after-free, regex DoS

Outputs structured JSON findings. Generates PoCs when requested. Expects to be challenged by Honoré's Devil's Advocate process.

### `claude-rastignac.md` -- Reconnaissance Specialist

Named after the ambitious strategist from Balzac. Deployed first, before any vulnerability hunting. Produces a comprehensive code map:
- Repository overview (language, size, architecture)
- Attack surface classification (tiers 1-5 by exploitation severity)
- Top 30 priority files with line counts and git history evidence
- Expected bug yield calculation (based on codebase size, maturity, language, security focus)
- Critical code patterns for Vautrin to look for

### `claude-bianchon.md` -- Documentation Librarian

Named after the methodical doctor from Balzac. Filters out false positives by checking whether flagged behaviors are documented features. For each finding, searches official docs, issue trackers, code comments, and configuration to classify as:
- `DOCUMENTED_FEATURE` -- Reject (it's intentional)
- `UNDOCUMENTED` -- Proceed with validation
- `UNCLEAR` -- Flag for human review

Prevents the common problem of reporting features as vulnerabilities (e.g., `--as-root` runs as root).

### `claude-lousteau.md` -- Memory Palace Custodian

Named after Étienne Lousteau, the jaded journalist from *Illusions Perdues*. Maintains all seven halls of the Memory Palace and provides historical context for every finding. Cynical, world-weary, has seen every bug pattern before.

Responsibilities:
- Pattern recognition — searches git archaeology for historical precedent
- Cynical oracle — predicts maintainer responses based on culture profiles
- Historical contextualization — annotates every bug with its genealogy across codebases
- Post-mortem archiving — records HIL feedback, evolves patterns, tracks statistics
- Cross-codebase tunneling — links identical patterns found in different projects

Primary user of the `n184-palace` CLI. While other agents write to the palace, Lousteau reads, cross-references, and annotates.

### `claude-fil-de-soie.md` -- Memory-Bug Specialist

Named after Fil-de-Soie ("silk thread"), real name Sélérier, a thief in Vautrin's gang who appears in *Splendeurs et misères des courtisanes*. The pickpocket of the heap — light, quiet, focused.

Scope is narrow on purpose: C/C++ memory management bugs only. Heap overflows, integer-overflow-driven undersized allocations, use-after-free, double-free, missing secret wipes, allocator-contract violations. The baseline of "correct" is OpenBSD's hardened libc (`reallocarray`, `recallocarray`, `freezero`, `malloc_conceal`), which gives a concrete principled standard to measure every allocation against.

Responsibilities:
- Inventory every allocation site in the target codebase
- Follow each allocation thread from creation through use to free
- Match against the N184-XXX risk-pattern catalog (see `souls/refs/malloc-hardening.md`)
- Front-load every fact into a scan-cache JSON dump so Honoré's bounded Devil's Advocate pipeline can render verdicts without a dialogue refresh
- Compose a clean Markdown report (`~/.n184/scan-cache/<scan_id>-report.md`) that a non-LLM-fluent maintainer can read and act on

Designed for standalone invocation via `./action --pull-the-thread --target <repo>` so operators who don't want to dance with Honoré can still get a focused memory-safety report.

---

## `action` -- Standalone Verb-Dispatch CLI

A generalized verb-dispatch CLI for invoking specialist agents on a target codebase without engaging the orchestrator dialogue. One verb per invocation, one target directory, one report at the end.

Wired verbs:
- `--pull-the-thread` → Fil-de-Soie (memory-bug scan)
- `--reconnoiter` → Rastignac (recon and code map)
- `--hunt` → Vautrin (general vuln hunt)
- `--consult-docs` → Bianchon (documentation cross-check)
- `--remember` → Lousteau (memory-palace lookup)

Two modes:
- `--mode local` (default): invokes the `claude` CLI directly with the agent's soul as the appended system prompt. No k8s required.
- `--mode k8s`: pushes a `schedule_task` envelope onto `n184:tasks` so the controller dispatches a Job through the standard pipeline.

Adding a new specialist agent is one entry in the `VERBS` registry at the top of the `action` script plus a soul file in `souls/claude-<agent>.md`.

---

## Running N184 -- `start.sh` / `stop.sh`

N184 runs as a podman-compose stack plus a host-side controller. This replaced
the old single-container `init.sh`/NanoClaw bootstrap.

**`start.sh`** brings everything up (idempotent — safe to re-run):

1. Ensures the podman machine is running
2. Builds the agent image (`container/build.sh`) if missing (`--build` forces it)
3. `podman compose up -d` — starts **Honoré + Redis + ChromaDB**
4. Creates the controller's python3.12 venv on first run
5. Starts exactly one **controller** (host process) — a duplicate would fight
   over the Telegram `getUpdates` poll and both would fail

**`stop.sh`** stops the controller and runs `podman compose down` (the `./data/`
directory is preserved).

### The two layers

- **Compose stack** (in podman, defined in `compose.yaml`): Honoré (persistent,
  subscribes to Redis), Redis (IPC + budget/breaker state + work queues), and
  ChromaDB (vector store).
- **Controller** (on the host, `controller/main.py`): bridges Telegram ↔ Redis
  and spawns specialist sub-agents on demand via `podman run` (`controller/
  podman_runner.py`) — replacing the k8s Jobs + KEDA of the legacy `k8s/` path.
  It runs on the host because host-side podman is the reliable path on macOS.

## `data/` -- Honoré's Persistent State

A host directory bind-mounted into the containers (replaces the old `build/`
runtime artifacts):

- `data/palace/` -- Memory Palace (findings, lessons, the `/sorrow` pot still)
- `data/sessions/` -- Claude Code session continuity
- `data/chroma/`, `data/redis/` -- vector store + runtime state

Because it is a plain host folder it survives `compose down` and even a
`podman system reset`, and you back it up by copying `data/`. It is gitignored.

---

## Documentation Files

### `README.md` -- Project Introduction

Covers what N184 is, system requirements (Podman, podman compose, Python 3.12, API keys), quick start guide (clone, configure .env, run ./start.sh, talk to Honoré via Telegram), and the Balzac agent naming convention.

### `FAQ.md` -- Frequently Asked Questions

Addresses common questions: getting started, accuracy, cost ($10-$100+ per audit), false positive rates, model independence, air-gapped operation, disclosure policy, comparison to Glasswing ($100M vs $300), and future plans.

### `ROADMAP.md` -- Feature Roadmap

- **v1.1 (Q2 2026)**: SQLite pattern database, report brevity, AST pattern flexibility
- **v1.2 (Q3 2026)**: Multi-language support (Rust, Python, Go, JS)
- **v1.3 (Q4 2026)**: CI integration (GitHub Actions, GitLab CI)
- **v2.0 (2027)**: Shared pattern marketplace, Memory Palace architecture
- **v2.1+**: Static+dynamic analysis fusion, local LLM support, supply chain analysis

Includes lessons learned from the OpenBSD experience (2 confirmed bugs, verbose output criticism).

### `SCOREBOARD.md` -- Bug Bounty Track Record

Tracks verified bugs found by N184:

| Project | Bugs Found | Status |
|---------|-----------|--------|
| Apple MLX | 10 | All fixed/confirmed |
| Apache httpd | 1 | Confirmed |
| Docker CLI | 1 | Confirmed by Docker Security |
| ClickHouse | 1 | Confirmed |
| OpenBSD | 2 | Fixed in-tree |

16 total findings across 5 projects.

### `LICENSE` -- AGPL v3

Standard GNU Affero General Public License v3.0 with a custom addendum:
- Encourages donations to Habitat for Humanity
- Forbids naming agents after Boston sports teams (unless you donate $10,000 to Habitat for Humanity)

### `SCOREBOARD.md~` -- Editor Backup

Backup file created by a text editor. Can be safely deleted.

### `N184 Presentation v2.pdf` -- Presentation Deck

Slide deck for presenting N184.

---

## How It All Fits Together

```
Human (HIL) <--Telegram--> Controller (host process)
                                |  bridges Telegram <-> Redis;
                                |  spawns sub-agents via `podman run`
                                v
       Redis  <------------>  Honoré (container, orchestrator)
                               /   |    \     \        \
                        Rastignac  |  Bianchon Goriot  Lousteau
                         (recon)   |   (docs)  (cons.) (memory)
                                   |
                             Vautrin Swarm
                       (controller-scaled hunters,
                        different AI models)
                                   |
                                   v
                       Memory Palace (./data/palace)
                           /              \
                     SQLite DB         ChromaDB
                  (relationships)    (7 halls of
                                   verbatim docs)
```

1. Human gives Honoré a repo to analyze
2. Honoré dispatches Rastignac to map the codebase
3. Rastignac produces a code map with prioritized files
4. Honoré dispatches Vautrin swarm (multiple models) targeting those files
5. Bianchon checks findings against documentation (feature vs bug)
6. Lousteau searches the Memory Palace for historical precedent and cross-codebase patterns
7. Goriot validates consensus across models (2/3 threshold)
8. Honoré applies Devil's Advocate filtering
9. Filtered findings presented to human for review
10. Human feedback goes to Lousteau, who archives it in the Memory Palace
