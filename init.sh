#!/bin/bash
set -euo pipefail

# canonical_path — return the on-disk casing of $1, resolving symlinks.
# Critical on case-insensitive filesystems (default macOS APFS): without
# canonicalization, the same checkout entered as `/Users/x/Code` vs
# `/Users/x/code` produces different sha1 install-slugs, which splits the
# launchd service registration from the locally rebuilt container image
# tag. Falls back to the input when realpath isn't available (very old
# systems) — case-sensitive filesystems don't need canonicalization.
canonical_path() {
  if command -v realpath >/dev/null 2>&1; then
    realpath "$1" 2>/dev/null || printf '%s' "$1"
  else
    printf '%s' "$1"
  fi
}

# Capture script directory before any cd. Canonicalized so downstream paths
# (PROJECT_ROOT, container/build.sh, install-slug computations) all see the
# same on-disk casing regardless of how the user invoked us.
SCRIPT_DIR="$(canonical_path "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"

# init.sh — Non-interactive NanoClaw initialization
# Builds everything from scratch, registers a main group, and optionally
# drops you into an interactive Claude Code shell inside the container.
#
# Prerequisites: Node.js >= 20, a container runtime (docker/podman/container)
# Configuration: Copy .env.example to .env and fill in values before running.

# ── NanoClaw Repository Setup ───────────────────────────────────────────────

# Default NanoClaw repository URL (can be overridden via NANOCLAW_REPO_URL env var)
NANOCLAW_REPO_URL="${NANOCLAW_REPO_URL:-https://github.com/qwibitai/nanoclaw.git}"

# Major version this script targets. v1 and v2 are not interchangeable —
# v2 was a ground-up rewrite (different DB layout, different setup steps,
# different channel-install pattern). Override with NANOCLAW_REQUIRED_MAJOR
# in .env when this script is updated for a future major.
NANOCLAW_REQUIRED_MAJOR="${NANOCLAW_REQUIRED_MAJOR:-2}"

# Check if we're already in a NanoClaw directory
if [ -f "package.json" ] && grep -q '"name".*"nanoclaw"' package.json 2>/dev/null; then
  # Already in NanoClaw directory
  PROJECT_ROOT="$(canonical_path "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
  cd "$PROJECT_ROOT"
else
  # Not in NanoClaw directory - need to clone it
  echo "NanoClaw not found. Cloning from $NANOCLAW_REPO_URL..."

  # Clone to ./nanoclaw relative to where init.sh was run.
  # Check for package.json (not just the directory) to detect broken partial clones.
  if [ ! -f "nanoclaw/package.json" ]; then
    rm -rf nanoclaw
    if ! git clone "$NANOCLAW_REPO_URL" nanoclaw; then
      echo "Failed to clone NanoClaw repository."
      echo "Make sure you have access to: $NANOCLAW_REPO_URL"
      echo "Or set NANOCLAW_REPO_URL to a different repository."
      exit 1
    fi
  fi

  # CD into cloned directory
  cd nanoclaw
  PROJECT_ROOT="$(canonical_path "$(pwd)")"
  echo "✓ NanoClaw cloned successfully"

  # Copy .env from script directory into the nanoclaw directory if not already present
  if [ ! -f "$PROJECT_ROOT/.env" ] && [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$PROJECT_ROOT/.env"
    echo "✓ .env copied from $SCRIPT_DIR"
  fi
fi

# ── Cleanup on exit ──────────────────────────────────────────────────────────

cleanup() {
  if [ -n "${RUNTIME:-}" ]; then
    # Kill any hung nanoclaw containers
    for name in $($RUNTIME ps --filter name=nanoclaw- --format '{{.Names}}' 2>/dev/null || true); do
      $RUNTIME stop -t 1 "$name" 2>/dev/null || true
    done
  fi
}

# Register cleanup on exit (normal exit, Ctrl+C, kill, etc.)
trap cleanup EXIT INT TERM

# ── Helpers ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; exit 1; }

# ── Detect container runtime ────────────────────────────────────────────────

detect_runtime() {
  # Prefer CONTAINER_RUNTIME from .env or environment
  if [ -n "${CONTAINER_RUNTIME:-}" ]; then
    if command -v "$CONTAINER_RUNTIME" >/dev/null 2>&1; then
      echo "$CONTAINER_RUNTIME"
      return
    fi
    fail "CONTAINER_RUNTIME=$CONTAINER_RUNTIME is set but not found in PATH"
  fi

  # Auto-detect: prefer podman > docker > container (Apple Container)
  for rt in podman docker container; do
    if command -v "$rt" >/dev/null 2>&1; then
      echo "$rt"
      return
    fi
  done

  fail "No container runtime found. Install docker, podman, or Apple Container."
}

# Source .env if it exists (for CONTAINER_RUNTIME, ASSISTANT_NAME, etc.)
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

RUNTIME=$(detect_runtime)
ASSISTANT_NAME="${ASSISTANT_NAME:-Andy}"

# ── OneCLI Gateway Check ──────────────────────────────────────────────────

USE_ONECLI=false
if [ -n "${ONECLI_URL:-}" ]; then
  if curl -sf --max-time 3 "$ONECLI_URL" >/dev/null 2>&1; then
    USE_ONECLI=true
  else
    warn "OneCLI gateway at $ONECLI_URL is not reachable — falling back to .env credentials"
  fi
fi

# ── Pre-flight checks ───────────────────────────────────────────────────────

CRED_MODE=".env"
if [ "$USE_ONECLI" = true ]; then
  CRED_MODE="OneCLI ($ONECLI_URL)"
fi

echo ""
echo -e "${BOLD}NanoClaw Init${NC}"
echo -e "Runtime: ${BOLD}$RUNTIME${NC}  Assistant: ${BOLD}$ASSISTANT_NAME${NC}  Credentials: ${BOLD}$CRED_MODE${NC}"
echo ""

# Check Node.js
if ! command -v node >/dev/null 2>&1; then
  fail "Node.js not found. Install Node.js >= 20."
fi
NODE_VERSION=$(node --version | sed 's/^v//')
NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 20 ]; then
  fail "Node.js $NODE_VERSION is too old. Need >= 20."
fi
ok "Node.js $NODE_VERSION"

# Check container runtime is working
if ! $RUNTIME info >/dev/null 2>&1; then
  fail "$RUNTIME is installed but not running. Start it and try again."
fi
ok "$RUNTIME is running"

# Check .env exists
if [ ! -f "$PROJECT_ROOT/.env" ]; then
  warn "No .env file found."
  echo "  Copy the example and fill in your values:"
  echo "    cp .env.example .env"
  echo "    \$EDITOR .env"
  echo ""
  fail "Create .env before running init.sh"
fi
ok ".env found"

# ── Verify NanoClaw major version ────────────────────────────────────────
# This script targets v2's layout (data/v2.db, --platform-id flag, channels-
# branch install pattern). A v1 clone — or a future v3 with breaking changes
# — will silently misbehave (steps "succeed" via `|| true` in the pipelines
# but write nothing). Catch the mismatch up front, before doing any work.
if ! NANOCLAW_VERSION=$(node -p "require('$PROJECT_ROOT/package.json').version" 2>/dev/null); then
  fail "Could not read NanoClaw version from $PROJECT_ROOT/package.json"
fi
NANOCLAW_MAJOR="${NANOCLAW_VERSION%%.*}"
if [ "$NANOCLAW_MAJOR" != "$NANOCLAW_REQUIRED_MAJOR" ]; then
  fail "NanoClaw v${NANOCLAW_VERSION} found, but init.sh targets v${NANOCLAW_REQUIRED_MAJOR}.x. Update the script or set NANOCLAW_REQUIRED_MAJOR in .env."
fi
ok "NanoClaw v${NANOCLAW_VERSION}"

# ── Patch install-slug for case-insensitive filesystems ─────────────────────
# Upstream's install-slug helpers hash process.cwd() / $PROJECT_ROOT directly,
# so on case-insensitive filesystems (default macOS APFS) the same checkout
# entered as `/Users/x/Code` vs `/Users/x/code` produces different slugs —
# splitting the launchd-registered service's expected image tag from the one
# the local rebuild actually applies. Symptom: podman pulls from docker.io
# instead of finding the local image, exits 125, and the service dies.
#
# The fix is one-line on each side: canonicalize before hashing.
# Applied idempotently here (no-op if upstream has already merged this).
patch_install_slug() {
  local ts="$PROJECT_ROOT/src/install-slug.ts"
  local sh="$PROJECT_ROOT/setup/lib/install-slug.sh"
  local patched=false

  if [ -f "$ts" ] && ! grep -q "realpathSync" "$ts"; then
    node - "$ts" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
if (!s.includes("realpathSync")) {
  s = s.replace(
    "import { createHash } from 'crypto';",
    "import { createHash } from 'crypto';\nimport { realpathSync } from 'fs';\n\n" +
    "function canonicalize(p: string): string {\n" +
    "  // On case-insensitive filesystems (default macOS APFS), process.cwd()\n" +
    "  // preserves whatever casing the process was launched with — so the same\n" +
    "  // checkout entered as different casings would otherwise hash to different\n" +
    "  // slugs. realpathSync.native() returns the on-disk casing.\n" +
    "  try { return realpathSync.native(p); } catch { return p; }\n" +
    "}"
  );
  s = s.replace(
    /\.update\(projectRoot\)/,
    ".update(canonicalize(projectRoot))"
  );
  fs.writeFileSync(p, s);
}
NODE_EOF
    patched=true
  fi

  if [ -f "$sh" ] && ! grep -q "realpath " "$sh"; then
    node - "$sh" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
if (!s.includes("realpath ")) {
  s = s.replace(
    /(local root="\$\{NANOCLAW_PROJECT_ROOT:-\$\{PROJECT_ROOT:-\$PWD\}\}")/,
    "$1\n  # Canonicalize so case-insensitive filesystems (default macOS APFS)\n" +
    "  # hash the same on-disk path regardless of casing. Mirrors\n" +
    "  # realpathSync.native() in install-slug.ts.\n" +
    "  local canonical=\"$root\"\n" +
    "  if command -v realpath >/dev/null 2>&1; then\n" +
    "    canonical=$(realpath \"$root\" 2>/dev/null) || canonical=\"$root\"\n" +
    "  fi"
  );
  // swap the variable used by the hashers
  s = s.replace(/printf '%s' "\$root" \| (shasum|sha1sum|od )/g, "printf '%s' \"$canonical\" | $1");
  fs.writeFileSync(p, s);
}
NODE_EOF
    patched=true
  fi

  if [ "$patched" = true ]; then
    ok "Patched install-slug for case-insensitive filesystems"
  fi
}
patch_install_slug

# ── Patch OneCLI to be opt-in ────────────────────────────────────────────────
# Upstream nanoclaw v2 unconditionally instantiates and calls into the OneCLI
# SDK at runtime (container-runner.ts, modules/approvals/index.ts). On hosts
# without OneCLI running, the SDK falls back to http://127.0.0.1:10254 and
# burns connect-timeouts on every container spawn + emits a steady stream
# of poll-failure logs from the approval handler. Gate both call sites on
# ONECLI_URL being configured so users with credentials in .env aren't
# paying for a gateway they don't run. Idempotent — no-op on re-runs.
patch_onecli_optin() {
  local cr="$PROJECT_ROOT/src/container-runner.ts"
  local ai="$PROJECT_ROOT/src/modules/approvals/index.ts"
  local patched=false

  if [ -f "$cr" ] && ! grep -q "if (ONECLI_URL) {" "$cr"; then
    node - "$cr" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
if (!s.includes("if (ONECLI_URL) {")) {
  // Bracket the existing OneCLI block: open `if (ONECLI_URL) {` before its
  // leading comment, then add an `else` branch that injects Anthropic auth
  // env vars from .env directly. Without this fallback, a non-OneCLI install
  // (no gateway, no Apple Container) starts containers with no credentials
  // and Claude Code immediately returns "Not logged in".
  const before = s;
  if (!s.includes("import { readEnvFile }")) {
    s = s.replace(
      /(import \{[^}]*\} from '\.\/config\.js';\n)/,
      "$1import { readEnvFile } from './env.js';\n"
    );
  }
  s = s.replace(
    "  // OneCLI gateway — injects HTTPS_PROXY",
    "  if (ONECLI_URL) {\n  // OneCLI gateway — injects HTTPS_PROXY"
  );
  s = s.replace(
    "  // Host gateway\n",
    "  } else {\n" +
    "    // Fallback: inject Anthropic auth env vars from .env directly when\n" +
    "    // OneCLI is not configured. Claude Code inside the container reads\n" +
    "    // these natively. Less secure than the OneCLI proxy (token visible\n" +
    "    // to processes inside the container) — fine for single-operator local\n" +
    "    // installs, swap to OneCLI for shared/production hosts.\n" +
    "    const dotenv = readEnvFile([\n" +
    "      'CLAUDE_CODE_OAUTH_TOKEN',\n" +
    "      'ANTHROPIC_API_KEY',\n" +
    "      'ANTHROPIC_AUTH_TOKEN',\n" +
    "      'ANTHROPIC_BASE_URL',\n" +
    "    ]);\n" +
    "    let injected = 0;\n" +
    "    for (const [key, value] of Object.entries(dotenv)) {\n" +
    "      args.push('-e', `${key}=${value}`);\n" +
    "      injected++;\n" +
    "    }\n" +
    "    if (injected > 0) {\n" +
    "      log.info('Anthropic auth injected from .env', { containerName, vars: Object.keys(dotenv) });\n" +
    "    } else {\n" +
    "      log.warn('No Anthropic auth in .env — container will have no credentials', { containerName });\n" +
    "    }\n" +
    "  }\n\n  // Host gateway\n"
  );
  if (s !== before) fs.writeFileSync(p, s);
}
NODE_EOF
    patched=true
  fi

  if [ -f "$ai" ] && ! grep -q "if (ONECLI_URL)" "$ai"; then
    node - "$ai" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
if (!s.includes("if (ONECLI_URL)")) {
  // Add the import if missing.
  if (!s.includes("from '../../config.js'")) {
    s = s.replace(
      "import { onDeliveryAdapterReady } from '../../delivery.js';",
      "import { ONECLI_URL } from '../../config.js';\nimport { onDeliveryAdapterReady } from '../../delivery.js';"
    );
  }
  // Gate the start/stop calls on ONECLI_URL being configured.
  s = s.replace(
    /onDeliveryAdapterReady\(\(adapter\) => \{\s*\n\s*startOneCLIApprovalHandler\(adapter\);\s*\n\s*\}\);/,
    "onDeliveryAdapterReady((adapter) => {\n  if (ONECLI_URL) {\n    startOneCLIApprovalHandler(adapter);\n  }\n});"
  );
  s = s.replace(
    /onShutdown\(\(\) => \{\s*\n\s*stopOneCLIApprovalHandler\(\);\s*\n\s*\}\);/,
    "onShutdown(() => {\n  if (ONECLI_URL) {\n    stopOneCLIApprovalHandler();\n  }\n});"
  );
  fs.writeFileSync(p, s);
}
NODE_EOF
    patched=true
  fi

  if [ "$patched" = true ]; then
    ok "Patched OneCLI to be opt-in (gated on ONECLI_URL)"
  fi
}
patch_onecli_optin

# ── Patch agent-runner poll loop to end query per turn ──────────────────────
# Upstream poll-loop tries to keep a single Claude SDK query alive across
# multiple turns by push()ing into MessageStream. After the SDK emits its
# first 'result', it stops iterating MessageStream — subsequent push() calls
# are silently queued, the for-await on query.events blocks forever waiting
# for events that never come, the heartbeat freezes, and Honoré only ever
# replies to the first DM.
#
# The fix is two lines: after dispatching the result text, call `query.end()`
# and `break` out of the for-await. The outer while-loop opens a fresh query
# with `resume: continuation` for the next turn — keeping the prompt cache
# warm at the cost of one SDK reconnect per turn (vs. broken-forever).
patch_poll_loop_per_turn() {
  local pl="$PROJECT_ROOT/container/agent-runner/src/poll-loop.ts"
  if [ ! -f "$pl" ]; then return 0; fi
  if grep -q "query.end();" "$pl"; then return 0; fi

  node - "$pl" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
if (!s.includes("query.end();")) {
  const before = s;
  s = s.replace(
    /(if \(event\.text\) \{\s*\n\s*dispatchResultText\(event\.text, routing\);\s*\n\s*\})\s*\n(\s*\}\s*\n\s*\}\s*\n\s*\} finally)/,
    "$1\n        // End the query and exit the for-await. The outer poll-loop\n" +
    "        // will see any new pending messages and open a fresh query with\n" +
    "        // the persisted continuation. Without this, the SDK stops iterating\n" +
    "        // MessageStream after the first 'result' and the runner hangs.\n" +
    "        query.end();\n        break;\n$2"
  );
  if (s !== before) fs.writeFileSync(p, s);
}
NODE_EOF

  if grep -q "query.end();" "$pl"; then
    ok "Patched poll-loop to end query per turn (Bug 5)"
  fi
}
patch_poll_loop_per_turn

# ── Patch agent-runner inbound DB to reopen per call ────────────────────────
# bun:sqlite caches read pages on first open. On a virtiofs mount (podman on
# macOS) those pages don't invalidate when the host writes to inbound.db, so
# the cached readonly connection sees an indefinitely-stale snapshot — host
# INSERTs new pending rows, container's getPendingMessages returns 0 forever,
# user sees "typing…" for many minutes. Reopen on every call (1–2 Hz, sub-ms
# overhead) bypasses the snapshot. Test fixtures via initTestSessionDb are
# preserved by an _inboundIsTestInstance flag.
patch_inbound_reopen() {
  local cn="$PROJECT_ROOT/container/agent-runner/src/db/connection.ts"
  if [ ! -f "$cn" ]; then return 0; fi
  if grep -q "_inboundIsTestInstance" "$cn"; then return 0; fi

  node - "$cn" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
if (!s.includes("_inboundIsTestInstance")) {
  const before = s;

  // 1. Add the flag declaration after `let _inbound: Database | null = null;`
  s = s.replace(
    "let _inbound: Database | null = null;\nlet _outbound:",
    "let _inbound: Database | null = null;\nlet _inboundIsTestInstance = false;\nlet _outbound:"
  );

  // 2. Replace the body of getInboundDb with the close+reopen logic.
  s = s.replace(
    /export function getInboundDb\(\): Database \{\s*\n\s*if \(!_inbound\) \{\s*\n\s*_inbound = new Database\(DEFAULT_INBOUND_PATH, \{ readonly: true \}\);\s*\n\s*_inbound\.exec\('PRAGMA busy_timeout = 5000'\);\s*\n\s*\}\s*\n\s*return _inbound;\s*\n\s*\}/,
    "export function getInboundDb(): Database {\n" +
    "  // Reopen on every call. bun:sqlite caches pages on first open; on a\n" +
    "  // virtiofs mount (podman/macOS) the cache doesn't invalidate when the\n" +
    "  // host writes to inbound.db, so a long-lived readonly connection sees\n" +
    "  // a stale snapshot for minutes. Closing + reopening forces a fresh fd.\n" +
    "  // ~1-2 Hz call rate; overhead is a SQLite header read, sub-millisecond.\n" +
    "  // Test instances seeded via initTestSessionDb are preserved.\n" +
    "  if (_inbound && !_inboundIsTestInstance) {\n" +
    "    try { _inbound.close(); } catch { /* ignore */ }\n" +
    "    _inbound = null;\n" +
    "  }\n" +
    "  if (!_inbound) {\n" +
    "    _inbound = new Database(DEFAULT_INBOUND_PATH, { readonly: true });\n" +
    "    _inbound.exec('PRAGMA busy_timeout = 5000');\n" +
    "  }\n" +
    "  return _inbound;\n" +
    "}"
  );

  // 3. In initTestSessionDb, set the test flag right after creating _inbound.
  s = s.replace(
    /(export function initTestSessionDb[\s\S]*?_inbound = new Database\(':memory:'\);)\s*\n/,
    "$1\n  _inboundIsTestInstance = true;\n"
  );

  // 4. In closeSessionDb, clear the test flag when nulling _inbound.
  s = s.replace(
    /(export function closeSessionDb\(\): void \{\s*\n\s*_inbound\?\.close\(\);\s*\n\s*_inbound = null;)\s*\n/,
    "$1\n  _inboundIsTestInstance = false;\n"
  );

  if (s !== before) fs.writeFileSync(p, s);
}
NODE_EOF

  if grep -q "_inboundIsTestInstance" "$cn"; then
    ok "Patched inbound DB to reopen per call (Bug 1: stale-read on virtiofs)"
  fi
}
patch_inbound_reopen

# ── Patch poll-loop to touch heartbeat every iteration ──────────────────────
# touchHeartbeat() was only called inside the for-await over query events.
# A container that completed a turn and went back to polling would never
# refresh /workspace/.heartbeat, so host-sweep couldn't distinguish a
# healthy idle container from a hung one. Move it to the top of the outer
# while-true so it's unconditional.
patch_poll_loop_heartbeat() {
  local pl="$PROJECT_ROOT/container/agent-runner/src/poll-loop.ts"
  if [ ! -f "$pl" ]; then return 0; fi
  # Idempotency marker: the comment block we insert.
  if grep -q "// Touch heartbeat every iteration" "$pl"; then return 0; fi

  node - "$pl" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
const marker = "    // Skip system messages — they're responses for MCP tools";
if (s.includes(marker) && !s.includes("// Touch heartbeat every iteration")) {
  const before = s;
  s = s.replace(
    marker,
    "    // Touch heartbeat every iteration so the host sweep can distinguish\n" +
    "    // a healthy idle container from a hung one. Previously only query\n" +
    "    // events refreshed it, so a container that returned to polling after\n" +
    "    // a turn looked identical to a stuck process.\n" +
    "    touchHeartbeat();\n\n" +
    marker
  );
  if (s !== before) fs.writeFileSync(p, s);
}
NODE_EOF

  if grep -q "// Touch heartbeat every iteration" "$pl"; then
    ok "Patched poll-loop to touch heartbeat every iteration (Bug 3)"
  fi
}
patch_poll_loop_heartbeat

# ── Patch claude provider to load CLAUDE.local.md ────────────────────────────
# Claude Agent SDK's `settingSources` option gates which instruction files
# load: 'project' = CLAUDE.md, 'local' = CLAUDE.local.md, 'user' = ~/.claude.
# Upstream nanoclaw v2 sets ['project', 'user'] only, omitting 'local' —
# so CLAUDE.local.md never gets read, which is exactly where the per-group
# persona lives (composer overwrites CLAUDE.md on every spawn). The Honoré
# soul sits on disk doing nothing without this fix.
patch_claude_local_settings() {
  local cp="$PROJECT_ROOT/container/agent-runner/src/providers/claude.ts"
  if [ ! -f "$cp" ]; then return 0; fi
  if grep -q "settingSources: \['project', 'local', 'user'\]" "$cp"; then return 0; fi

  node - "$cp" <<'NODE_EOF'
const fs = require('fs');
const p = process.argv[2];
let s = fs.readFileSync(p, 'utf8');
const before = s;
s = s.replace(
  "settingSources: ['project', 'user'],",
  "settingSources: ['project', 'local', 'user'],"
);
if (s !== before) fs.writeFileSync(p, s);
NODE_EOF

  if grep -q "settingSources: \['project', 'local', 'user'\]" "$cp"; then
    ok "Patched claude provider to load CLAUDE.local.md (soul wiring)"
  fi
}
patch_claude_local_settings

# ── Fresh start check ───────────────────────────────────────────────────────

NEEDS_CLEAN=false
if [ -f "$PROJECT_ROOT/data/v2.db" ] || [ -d "$PROJECT_ROOT/dist" ] || [ -d "$PROJECT_ROOT/data/v2-sessions" ]; then
  NEEDS_CLEAN=true
fi

if [ "$NEEDS_CLEAN" = true ]; then
  echo ""
  warn "Existing installation detected."
  echo "  This will remove: data/v2.db, data/v2-sessions/, dist/, logs/"
  echo "  This will keep:   .env, groups/*, node_modules/"
  echo ""
  read -r -p "Start fresh? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi

  info "Cleaning previous state..."

  # Stop service and clear launchd/systemd state to avoid stale process issues
  if [ "$(uname -s)" = "Darwin" ]; then
    launchctl bootout "gui/$(id -u)/com.nanoclaw" 2>/dev/null || true
    launchctl unload ~/Library/LaunchAgents/com.nanoclaw.plist 2>/dev/null || true
  else
    systemctl --user stop nanoclaw 2>/dev/null || true
    systemctl --user reset-failed nanoclaw 2>/dev/null || true
  fi

  # Kill orphaned containers
  for name in $($RUNTIME ps --filter name=nanoclaw- --format '{{.Names}}' 2>/dev/null || true); do
    $RUNTIME stop -t 1 "$name" 2>/dev/null || true
  done

  rm -f "$PROJECT_ROOT/data/v2.db"
  rm -rf "$PROJECT_ROOT/data/v2-sessions"
  rm -rf "$PROJECT_ROOT/dist"
  rm -rf "$PROJECT_ROOT/logs"
  ok "Cleaned"
fi

# ── Step 1: Install dependencies ────────────────────────────────────────────

info "Installing npm dependencies..."
if [ -f "$PROJECT_ROOT/package-lock.json" ]; then
  npm ci --silent 2>&1 | tail -1 || fail "npm ci failed. Check logs."
else
  npm install --silent 2>&1 | tail -1 || fail "npm install failed. Check logs."
fi

# Verify native module
if ! node -e "require('better-sqlite3')" 2>/dev/null; then
  fail "better-sqlite3 native module failed to load. Check build tools."
fi
ok "Dependencies installed"

# ── Step 1b: Install Telegram channel ─────────────────────────────────────
# Skipped unless TELEGRAM_BOT_TOKEN is set — there's no point installing the
# adapter for a user who isn't going to use it.
#
# Uses the canonical v2 install pattern from upstream qwibitai/nanoclaw's
# `origin/channels` branch (mirrors that repo's setup/add-telegram.sh):
# fetch the branch, copy the adapter source files, append a self-registration
# import to the channels barrel, install the pinned chat adapter package.
#
# We deliberately do NOT merge the legacy `qwibitai/nanoclaw-telegram` repo:
# it predates the v2 rewrite (removes the `chat` SDK dep, adds `grammy`, and
# brings in a telegram.ts whose imports — `logger`, `registry`, `Channel`,
# `OnChatMetadata` — reference symbols that no longer exist in v2). Merging
# it produces a tree that compiles-fails no matter how cleanly you resolve
# the conflicts.

# Pinned to keep init.sh in lockstep with what the channels branch ships.
# Cross-check origin/channels:setup/add-telegram.sh (ADAPTER_VERSION) when
# bumping nanoclaw.
TELEGRAM_ADAPTER_VERSION="@chat-adapter/telegram@4.26.0"

telegram_already_installed() {
  [ -f "$PROJECT_ROOT/src/channels/telegram.ts" ] && \
    grep -q "@chat-adapter/telegram" "$PROJECT_ROOT/src/channels/telegram.ts" 2>/dev/null && \
    grep -q "^import './telegram.js';" "$PROJECT_ROOT/src/channels/index.ts" 2>/dev/null
}

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  ok "Telegram channel skipped (TELEGRAM_BOT_TOKEN not set in .env)"
elif telegram_already_installed; then
  ok "Telegram channel already installed"
else
  info "Installing Telegram channel from origin/channels..."

  if ! git -C "$PROJECT_ROOT" fetch origin channels >/dev/null 2>&1; then
    fail "Failed to fetch origin/channels — needed for the Telegram adapter."
  fi

  for f in \
    src/channels/telegram.ts \
    src/channels/telegram-pairing.ts \
    src/channels/telegram-pairing.test.ts \
    src/channels/telegram-markdown-sanitize.ts \
    src/channels/telegram-markdown-sanitize.test.ts
  do
    if ! git -C "$PROJECT_ROOT" show "origin/channels:$f" > "$PROJECT_ROOT/$f" 2>/dev/null; then
      fail "Could not extract $f from origin/channels"
    fi
  done

  # Append the self-registration import to the channels barrel so the
  # registerChannelAdapter() side effect runs at startup.
  if ! grep -q "^import './telegram.js';" "$PROJECT_ROOT/src/channels/index.ts"; then
    echo "import './telegram.js';" >> "$PROJECT_ROOT/src/channels/index.ts"
  fi

  info "Installing $TELEGRAM_ADAPTER_VERSION..."
  npm install --silent "$TELEGRAM_ADAPTER_VERSION" 2>&1 | tail -1 || \
    fail "npm install $TELEGRAM_ADAPTER_VERSION failed."
  ok "Telegram channel installed ($TELEGRAM_ADAPTER_VERSION)"
fi

# ── Step 1c: Patch container runtime to use absolute path ────────────────
# Done AFTER all git merges so the patch isn't overwritten.

CONTAINER_RUNTIME_TS="$PROJECT_ROOT/src/container-runtime.ts"
if [ -f "$CONTAINER_RUNTIME_TS" ]; then
  if grep -q "^export const CONTAINER_RUNTIME_BIN = 'docker'" "$CONTAINER_RUNTIME_TS"; then
    # Resolve the full absolute path to the runtime binary so it works
    # under launchd/systemd where PATH may not include the runtime's directory.
    RUNTIME_PATH="$(which "$RUNTIME")"
    info "Patching container-runtime.ts to use $RUNTIME_PATH..."
    awk -v rpath="$RUNTIME_PATH" '{
      if ($0 ~ /^export const CONTAINER_RUNTIME_BIN = '"'"'docker'"'"'/) {
        print "export const CONTAINER_RUNTIME_BIN = '"'"'" rpath "'"'"';"
      } else {
        print $0
      }
    }' "$CONTAINER_RUNTIME_TS" > "${CONTAINER_RUNTIME_TS}.tmp" \
      && mv "${CONTAINER_RUNTIME_TS}.tmp" "$CONTAINER_RUNTIME_TS"
    ok "Container runtime set to $RUNTIME_PATH"
  else
    ok "Container runtime already patched"
  fi
else
  warn "src/container-runtime.ts not found — skipping runtime patch"
fi

# ── Step 2: Build TypeScript ────────────────────────────────────────────────

info "Building TypeScript..."
npm run build --silent 2>&1 || fail "TypeScript build failed."
ok "Built dist/"

# ── Step 3: Build container image ───────────────────────────────────────────

# Pre-flight: building the agent image installs claude-code globally via pnpm
# inside the runtime VM. On podman/macOS this has been observed OOM-killed
# (exit 137) when the machine has the default 2GB. Require >=4GB; offer to
# bump it for the user since stop/set/start is mechanical but destructive
# (the machine restarts), so confirm before doing it.
NANOCLAW_MIN_PODMAN_MEM_MIB="${NANOCLAW_MIN_PODMAN_MEM_MIB:-4096}"
if [ "$RUNTIME" = "podman" ]; then
  PODMAN_MEM_MIB=$(podman machine inspect --format '{{.Resources.Memory}}' 2>/dev/null | head -n1 | tr -d '[:space:]')
  if [[ "$PODMAN_MEM_MIB" =~ ^[0-9]+$ ]] && [ "$PODMAN_MEM_MIB" -lt "$NANOCLAW_MIN_PODMAN_MEM_MIB" ]; then
    echo ""
    warn "Podman machine has only ${PODMAN_MEM_MIB}MiB of memory."
    echo "  The container build needs more than 2GB to install @anthropic-ai/claude-code,"
    echo "  and will be OOM-killed (exit 137) on the default 2GB allocation."
    echo ""
    read -r -p "Bump podman machine memory to ${NANOCLAW_MIN_PODMAN_MEM_MIB} MiB now? (stops/restarts the machine) [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
      info "Stopping podman machine..."
      podman machine stop >/dev/null 2>&1 || fail "Failed to stop podman machine."
      info "Setting memory to ${NANOCLAW_MIN_PODMAN_MEM_MIB} MiB..."
      podman machine set --memory "$NANOCLAW_MIN_PODMAN_MEM_MIB" >/dev/null 2>&1 || fail "Failed to set podman machine memory."
      info "Starting podman machine..."
      podman machine start >/dev/null 2>&1 || fail "Failed to start podman machine."
      ok "Podman machine memory bumped to ${NANOCLAW_MIN_PODMAN_MEM_MIB} MiB"
    else
      echo "  Run these manually before re-running ./init.sh:"
      echo "    podman machine stop"
      echo "    podman machine set --memory ${NANOCLAW_MIN_PODMAN_MEM_MIB}"
      echo "    podman machine start"
      echo ""
      fail "Increase podman machine memory and re-run ./init.sh"
    fi
  fi
fi

info "Building container image (this may take a few minutes on first run)..."
CONTAINER_RUNTIME="$RUNTIME" "$PROJECT_ROOT/container/build.sh" latest 2>&1 | tail -3
ok "Container image built: nanoclaw-agent:latest"

# ── Step 4: Test container ──────────────────────────────────────────────────

info "Testing container..."
CONTAINER_TIMEOUT=30
TEST_TMPFILE=$(mktemp)
TEST_CONTAINER_NAME="N184_Honore_test-$$"

# Pass credentials to container (OneCLI gateway or .env vars)
CRED_ARGS=()
if [ "$USE_ONECLI" = true ]; then
  CRED_ARGS+=(-e "ONECLI_URL=$ONECLI_URL")
else
  for var in ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_AUTH_TOKEN ANTHROPIC_BASE_URL; do
    val="${!var:-}"
    if [ -n "$val" ]; then
      CRED_ARGS+=(-e "$var=$val")
    fi
  done
fi

# Run the container in the background with a watchdog that kills it after
# CONTAINER_TIMEOUT seconds.  Uses the named container so we can stop it
# reliably via the runtime — works on macOS without coreutils timeout.
echo '{"prompt":"Say hello","groupFolder":"test","chatJid":"test@init","isMain":false}' | \
  $RUNTIME run -i --rm --name "$TEST_CONTAINER_NAME" "${CRED_ARGS[@]}" \
  nanoclaw-agent:latest >"$TEST_TMPFILE" 2>/dev/null &
TEST_PID=$!

# Watchdog: kill the container after timeout
( sleep "$CONTAINER_TIMEOUT" && $RUNTIME stop -t 1 "$TEST_CONTAINER_NAME" 2>/dev/null ) &
WATCHDOG_PID=$!

wait "$TEST_PID" 2>/dev/null && TEST_EXIT=0 || TEST_EXIT=$?

# Kill watchdog if container finished before timeout
kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true

# Clean up container in case it's still around
$RUNTIME rm -f "$TEST_CONTAINER_NAME" 2>/dev/null || true

TEST_OUTPUT=$(cat "$TEST_TMPFILE")
rm -f "$TEST_TMPFILE"
if echo "$TEST_OUTPUT" | grep -q "NANOCLAW_OUTPUT"; then
  ok "Container runs successfully"
elif [ "$TEST_EXIT" -eq 137 ] || [ -z "$TEST_OUTPUT" ]; then
  warn "Container test timed out after ${CONTAINER_TIMEOUT}s (check credentials and network)"
else
  warn "Container test didn't produce expected output (may need credentials)"
fi

# ── Step 5: Pre-register filesystem prep ───────────────────────────────────
# The earlier comment claimed soul files had to be deployed BEFORE register
# so that register.ts would "see them and leave them alone". That's wrong —
# register.ts unconditionally rewrites groups/<folder>/CLAUDE.md from
# .claude-fragments/ on every run, silently stripping the soul. The actual
# main-group soul deploy is now in Step 5f after all register calls.
#
# Here we just create the directories register.ts expects to exist.

SOULS_DIR="$SCRIPT_DIR/souls"
HONORE_SOURCE="$SOULS_DIR/claude-honore.md"
MAIN_CLAUDE_MD="$PROJECT_ROOT/groups/main/CLAUDE.md"

mkdir -p "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs"
mkdir -p "$PROJECT_ROOT/groups/main/logs"

if [ ! -f "$HONORE_SOURCE" ]; then
  fail "claude-honore.md not found at $HONORE_SOURCE"
fi

# Mirror all souls under groups/main/souls/ for in-tree reference. register.ts
# doesn't touch this subdirectory, so it's safe to do this before the register
# step.
SOULS_DEST="$PROJECT_ROOT/groups/main/souls"
mkdir -p "$SOULS_DEST"

for soul in claude-honore.md claude-vautrin.md claude-rastignac.md claude-bianchon.md claude-lousteau.md; do
  if [ -f "$SOULS_DIR/$soul" ]; then
    cp "$SOULS_DIR/$soul" "$SOULS_DEST/$soul"
  else
    warn "Soul file $soul not found in $SOULS_DIR"
  fi
done
ok "Agent souls staged in groups/main/souls/ (Honoré, Vautrin, Rastignac, Bianchon, Lousteau)"

# ── Step 5b: Register main group ──────────────────────────────────────────

info "Registering main group..."

# Use the setup register step. Flags are v2 names: `--platform-id` (was
# `--jid` in v1) and there's no longer an `--is-main` concept — "main" is
# just a folder name in v2's entity model, not a privileged status.
npx tsx setup/index.ts --step register \
  --platform-id "main@init.local" \
  --name "Main" \
  --trigger "@${ASSISTANT_NAME}" \
  --folder "main" \
  --channel "cli" \
  --no-trigger-required \
  --assistant-name "$ASSISTANT_NAME" 2>&1 | grep -v "^===" || true
ok "Main group registered"

# ── Step 5c: Register Telegram chat ───────────────────────────────────────

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  TELEGRAM_CHAT_NAME="${TELEGRAM_CHAT_NAME:-Main}"
  # Bug 1 guard: if the user followed the old `tg:<id>` example, strip the
  # bogus prefix. Runtime expects the bare chat id; namespacedPlatformId()
  # adds the `telegram:` prefix itself, so `tg:` ends up double-prefixed
  # to `telegram:tg:<id>` and never matches inbound messages.
  TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID#tg:}"
  info "Registering Telegram chat $TELEGRAM_CHAT_ID..."
  # No --trigger for Telegram: register.ts defaults engage_pattern to '.'
  # for DMs (match every message) when --trigger is omitted. Passing
  # `--trigger "@${ASSISTANT_NAME}"` would force users to @-mention the
  # bot in their own DM — wrong for a personal assistant.
  npx tsx setup/index.ts --step register \
    --platform-id "$TELEGRAM_CHAT_ID" \
    --name "$TELEGRAM_CHAT_NAME" \
    --folder "main" \
    --channel "telegram" \
    --assistant-name "$ASSISTANT_NAME" 2>&1 | grep -v "^===" || true
  ok "Telegram chat registered ($TELEGRAM_CHAT_ID)"

  # Bug 3 fix: grant the operator owner role. Telegram messaging_groups
  # default to unknown_sender_policy='strict' — without an owner role,
  # every inbound DM is dropped as "unknown sender (strict policy)".
  # In a multi-user deployment this would normally come from
  # setup/pair-telegram.ts (interactive pairing-code flow). For
  # single-operator headless setup, a direct INSERT is the equivalent
  # claim: the .env owner has explicitly stated this is their chat.
  V2_DB="$PROJECT_ROOT/data/v2.db"
  if [ -f "$V2_DB" ] && command -v sqlite3 >/dev/null 2>&1; then
    OWNER_USER_ID="telegram:$TELEGRAM_CHAT_ID"
    EXISTING_OWNER=$(sqlite3 "$V2_DB" "SELECT user_id FROM user_roles WHERE user_id='$OWNER_USER_ID' AND role='owner' LIMIT 1;" 2>/dev/null || true)
    if [ -z "$EXISTING_OWNER" ]; then
      info "Granting owner role to $OWNER_USER_ID..."
      sqlite3 "$V2_DB" "INSERT OR IGNORE INTO users (id, kind, display_name) VALUES ('$OWNER_USER_ID', 'human', 'Operator');" 2>/dev/null || true
      sqlite3 "$V2_DB" "INSERT INTO user_roles (user_id, role, agent_group_id, granted_by, granted_at) VALUES ('$OWNER_USER_ID', 'owner', NULL, NULL, strftime('%Y-%m-%dT%H:%M:%fZ','now'));" 2>/dev/null \
        && ok "Owner role granted to $OWNER_USER_ID" \
        || warn "Could not grant owner role (schema may differ — run setup/pair-telegram.ts manually)"
    else
      ok "Owner role already granted to $OWNER_USER_ID"
    fi
  else
    warn "v2.db not found or sqlite3 missing — owner role NOT granted"
    echo "  Without an owner, strict unknown-sender policy will drop every inbound DM."
    echo "  Run: npx tsx setup/pair-telegram.ts (after first-message capture)"
  fi
elif [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  warn "TELEGRAM_BOT_TOKEN is set but TELEGRAM_CHAT_ID is missing from .env"
  echo "  After startup, send /chatid to your bot in Telegram to get the ID,"
  echo "  then add TELEGRAM_CHAT_ID=<chat-id> to .env and re-run init.sh"
  echo "  (just the digits — no 'tg:' prefix)"
fi

# ── Step 5d: Register sub-agent groups ──────────────────────────────────────
# Each N184 agent gets its own NanoClaw group with a dedicated container,
# IPC namespace, and separate logs for visibility.

for agent_data in \
  "vautrin@n184.local|Vautrin|n184-vautrin|@Vautrin|claude-vautrin.md" \
  "rastignac@n184.local|Rastignac|n184-rastignac|@Rastignac|claude-rastignac.md" \
  "bianchon@n184.local|Bianchon|n184-bianchon|@Bianchon|claude-bianchon.md" \
  "lousteau@n184.local|Lousteau|n184-lousteau|@Lousteau|claude-lousteau.md"; do

  IFS='|' read -r AGENT_PLATFORM_ID AGENT_NAME AGENT_FOLDER AGENT_TRIGGER AGENT_SOUL <<< "$agent_data"

  info "Registering $AGENT_NAME group..."
  npx tsx setup/index.ts --step register \
    --platform-id "$AGENT_PLATFORM_ID" \
    --name "$AGENT_NAME" \
    --trigger "$AGENT_TRIGGER" \
    --folder "$AGENT_FOLDER" \
    --channel "cli" \
    --no-trigger-required \
    --assistant-name "$AGENT_NAME" 2>&1 | grep -v "^===" || true

  # Deploy soul file as the group's CLAUDE.md
  AGENT_SOUL_SRC="$SOULS_DIR/$AGENT_SOUL"
  AGENT_GROUP_DIR="$PROJECT_ROOT/groups/$AGENT_FOLDER"
  mkdir -p "$AGENT_GROUP_DIR/logs"
  if [ -f "$AGENT_SOUL_SRC" ]; then
    cp "$AGENT_SOUL_SRC" "$AGENT_GROUP_DIR/CLAUDE.md"
    ok "$AGENT_NAME group registered (soul deployed to groups/$AGENT_FOLDER/)"
  else
    warn "Soul file $AGENT_SOUL not found at $AGENT_SOUL_SRC"
  fi
done

# ── Step 5e: Wire Honoré soul via CLAUDE.local.md ──────────────────────────
# CLAUDE.md is the wrong target: claude-md-compose.ts rewrites it on every
# container spawn from .claude-fragments, so any soul we copy in is gone by
# the next message. CLAUDE.local.md is the right place — composer leaves it
# alone (claude-md-compose.ts:127 only writes if missing), and Claude Code
# auto-loads it. A 1-line @-import keeps the soul file as the source of
# truth at groups/main/souls/claude-honore.md.
MAIN_CLAUDE_LOCAL_MD="$PROJECT_ROOT/groups/main/CLAUDE.local.md"
info "Wiring Honoré soul via groups/main/CLAUDE.local.md..."
echo "@./souls/claude-honore.md" > "$MAIN_CLAUDE_LOCAL_MD"
ok "CLAUDE.local.md → souls/claude-honore.md (Honoré persona)"

# ── Step 5f: Initialize Memory Palace ───────────────────────────────────────

info "Initializing N184 Memory Palace..."
mkdir -p "$HOME/.n184"
if command -v python3 >/dev/null 2>&1; then
  python3 "$SCRIPT_DIR/n184_palace_cli.py" init >/dev/null 2>&1 && \
    ok "Memory Palace initialized (~/.n184/)" || \
    warn "Memory Palace initialization failed (chromadb may not be installed on host)"
else
  warn "Python3 not found — Memory Palace will be initialized on first container run"
fi

# ── Step 6: Mount allowlist ─────────────────────────────────────────────────

MOUNT_CONFIG_DIR="$HOME/.config/nanoclaw"
MOUNT_CONFIG_FILE="$MOUNT_CONFIG_DIR/mount-allowlist.json"
mkdir -p "$MOUNT_CONFIG_DIR"

if [ ! -f "$MOUNT_CONFIG_FILE" ]; then
  info "Creating mount allowlist..."
  npx tsx setup/index.ts --step mounts --empty 2>&1 | grep -v "^===" || true
  ok "Mount allowlist created at $MOUNT_CONFIG_FILE"
else
  ok "Mount allowlist already exists"
fi

# ── Step 6b: Sync .env to container environment ──────────────────────────

info "Syncing .env to container environment..."
mkdir -p "$PROJECT_ROOT/data/env"
cp "$PROJECT_ROOT/.env" "$PROJECT_ROOT/data/env/env"
ok "Container environment synced"

# ── Step 7: Generate launcher wrapper ─────────────────────────────────────
# Create a wrapper script that sets up PATH before launching node.
# This is more portable than patching plist/systemd PATH values directly —
# it survives plist regeneration and works on any init system.

LAUNCHER="$PROJECT_ROOT/run.sh"
NODE_PATH="$(which node)"
RUNTIME_DIR="$(dirname "$(which "$RUNTIME")")"
NODE_DIR="$(dirname "$NODE_PATH")"

info "Generating launcher wrapper (run.sh)..."
cat > "$LAUNCHER" << LAUNCHER_EOF
#!/bin/bash
# Auto-generated by init.sh — ensures PATH includes runtime and node directories
# so the service works under launchd/systemd where PATH is minimal.
export PATH="$RUNTIME_DIR:$NODE_DIR:/usr/local/bin:/usr/bin:/bin:\$PATH"
exec "$NODE_PATH" "$PROJECT_ROOT/dist/index.js" "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
ok "Launcher wrapper created (run.sh)"

# ── Step 7b: Service setup ────────────────────────────────────────────────

info "Setting up background service..."
npx tsx setup/index.ts --step service 2>&1 | grep -v "^===" || true
ok "Service configured"

# ── Step 7c: Point service at launcher wrapper ───────────────────────────

if [ "$(uname -s)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.nanoclaw.plist"
  if [ -f "$PLIST" ]; then
    # Replace the ProgramArguments to use run.sh instead of calling node directly.
    # This ensures the service always has the right PATH regardless of what
    # the setup step generates.
    if ! grep -q "run.sh" "$PLIST"; then
      info "Pointing launchd service at run.sh wrapper..."
      sed -i '' "s|<string>${NODE_PATH}</string>|<string>${LAUNCHER}</string>|" "$PLIST"
      # Remove the node args line (dist/index.js) — run.sh handles it
      sed -i '' "\\|<string>${PROJECT_ROOT}/dist/index.js</string>|d" "$PLIST"
      ok "launchd plist updated to use run.sh"
    else
      ok "launchd plist already uses run.sh"
    fi
  fi
else
  # Linux systemd — update ExecStart if present
  UNIT="$HOME/.config/systemd/user/nanoclaw.service"
  if [ -f "$UNIT" ]; then
    if ! grep -q "run.sh" "$UNIT"; then
      info "Pointing systemd service at run.sh wrapper..."
      sed -i "s|ExecStart=.*|ExecStart=${LAUNCHER}|" "$UNIT"
      systemctl --user daemon-reload 2>/dev/null || true
      ok "systemd unit updated to use run.sh"
    else
      ok "systemd unit already uses run.sh"
    fi
  fi
fi

# ── Step 8: Start service ─────────────────────────────────────────────────
# Clean reload to avoid stale launchd/systemd state from previous runs.

info "Starting service..."
if [ "$(uname -s)" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.nanoclaw.plist"
  if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)/com.nanoclaw" 2>/dev/null || true
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST" 2>/dev/null || true
    ok "launchd service loaded"
  fi
else
  UNIT="$HOME/.config/systemd/user/nanoclaw.service"
  if [ -f "$UNIT" ]; then
    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user restart nanoclaw 2>/dev/null || true
    ok "systemd service restarted"
  fi
fi

# ── Done ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}NanoClaw initialized successfully.${NC}"
echo ""
echo "  Runtime:   $RUNTIME"
echo "  Assistant: $ASSISTANT_NAME"
echo "  Groups:    groups/main/"
echo "  Database:  data/v2.db"
echo "  Logs:      logs/nanoclaw.log"
echo ""
echo "Quick commands:"
echo "  npm run dev                    # Run with hot reload"
echo "  ./init.sh --shell              # Interactive Claude Code session"
echo "  claw \"Hello\"                   # Send a one-shot prompt"
echo ""

# ── Interactive shell (--shell flag or prompt) ──────────────────────────────

launch_shell() {
  info "Launching interactive Claude Code session in container..."
  echo "  (Ctrl+C to exit)"
  echo ""

  # Build mount args for main group. Path is the v2 session-data convention
  # (data/v2-sessions/) — note that v2 normally drives sessions through the
  # router/container-runner, not via direct `$RUNTIME run`; this side channel
  # is for ad-hoc interactive Claude Code shells, not the main service path.
  GROUP_DIR="$PROJECT_ROOT/groups/main"
  SESSION_DIR="$PROJECT_ROOT/data/v2-sessions/main"
  IPC_DIR="$PROJECT_ROOT/data/ipc/main"

  mkdir -p "$GROUP_DIR" "$SESSION_DIR/.claude" "$IPC_DIR"/{messages,tasks,input}

  # Copy agent-runner source if needed
  AGENT_SRC="$SESSION_DIR/agent-runner-src"
  if [ ! -d "$AGENT_SRC" ] && [ -d "$PROJECT_ROOT/container/agent-runner/src" ]; then
    cp -r "$PROJECT_ROOT/container/agent-runner/src" "$AGENT_SRC"
  fi

  MOUNT_ARGS=(
    -v "$PROJECT_ROOT:/workspace/project:ro"
    -v "$GROUP_DIR:/workspace/group"
    -v "$SESSION_DIR/.claude:/home/node/.claude"
    -v "$IPC_DIR:/workspace/ipc"
  )

  # Pass credentials for interactive mode (OneCLI gateway or .env vars)
  ENV_ARGS=()
  if [ "$USE_ONECLI" = true ]; then
    ENV_ARGS+=(-e "ONECLI_URL=$ONECLI_URL")
  else
    while IFS='=' read -r key value; do
      key=$(echo "$key" | xargs)
      [ -z "$key" ] || [[ "$key" == \#* ]] && continue
      value="${value%\"}"
      value="${value#\"}"
      value="${value%\'}"
      value="${value#\'}"
      case "$key" in
        CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|ANTHROPIC_BASE_URL|ANTHROPIC_AUTH_TOKEN)
          ENV_ARGS+=(-e "$key=$value")
          ;;
      esac
    done < "$PROJECT_ROOT/.env"
  fi

  $RUNTIME run -it --rm \
    --name N184_Honore \
    "${MOUNT_ARGS[@]}" \
    "${ENV_ARGS[@]}" \
    -e "ASSISTANT_NAME=$ASSISTANT_NAME" \
    --entrypoint claude \
    nanoclaw-agent:latest \
    --dangerously-skip-permissions
}

if [[ "${1:-}" == "--shell" ]]; then
  launch_shell
  exit 0
fi

# Offer interactive shell
echo -e "Launch an interactive Claude Code shell now? [y/N] "
read -r launch
if [[ "$launch" =~ ^[Yy]$ ]]; then
  launch_shell
fi
