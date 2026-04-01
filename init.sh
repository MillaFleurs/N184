#!/bin/bash
set -euo pipefail

# Capture script directory before any cd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# init.sh — Non-interactive NanoClaw initialization
# Builds everything from scratch, registers a main group, and optionally
# drops you into an interactive Claude Code shell inside the container.
#
# Prerequisites: Node.js >= 20, a container runtime (docker/podman/container)
# Configuration: Copy .env.example to .env and fill in values before running.

# ── NanoClaw Repository Setup ───────────────────────────────────────────────

# Default NanoClaw repository URL (can be overridden via NANOCLAW_REPO_URL env var)
NANOCLAW_REPO_URL="${NANOCLAW_REPO_URL:-https://github.com/qwibitai/nanoclaw.git}"

# Check if we're already in a NanoClaw directory
if [ -f "package.json" ] && grep -q '"name".*"nanoclaw"' package.json 2>/dev/null; then
  # Already in NanoClaw directory
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$PROJECT_ROOT"
else
  # Not in NanoClaw directory - need to clone it
  echo "NanoClaw not found. Cloning from $NANOCLAW_REPO_URL..."

  # Clone to ./nanoclaw relative to where init.sh was run
  if [ ! -d "nanoclaw" ]; then
    if ! git clone "$NANOCLAW_REPO_URL" nanoclaw; then
      echo "Failed to clone NanoClaw repository."
      echo "Make sure you have access to: $NANOCLAW_REPO_URL"
      echo "Or set NANOCLAW_REPO_URL to a different repository."
      exit 1
    fi
  fi

  # CD into cloned directory
  cd nanoclaw
  PROJECT_ROOT="$(pwd)"
  echo "✓ NanoClaw cloned successfully"
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

# ── Fresh start check ───────────────────────────────────────────────────────

NEEDS_CLEAN=false
if [ -f "$PROJECT_ROOT/store/messages.db" ] || [ -d "$PROJECT_ROOT/dist" ] || [ -d "$PROJECT_ROOT/data/sessions" ]; then
  NEEDS_CLEAN=true
fi

if [ "$NEEDS_CLEAN" = true ]; then
  echo ""
  warn "Existing installation detected."
  echo "  This will remove: store/, dist/, data/sessions/, logs/"
  echo "  This will keep:   .env, groups/*, node_modules/"
  echo ""
  read -r -p "Start fresh? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
  fi

  info "Cleaning previous state..."

  # Stop service if running
  if [ "$(uname -s)" = "Darwin" ]; then
    launchctl unload ~/Library/LaunchAgents/com.nanoclaw.plist 2>/dev/null || true
  else
    systemctl --user stop nanoclaw 2>/dev/null || true
  fi

  # Kill orphaned containers
  for name in $($RUNTIME ps --filter name=nanoclaw- --format '{{.Names}}' 2>/dev/null || true); do
    $RUNTIME stop -t 1 "$name" 2>/dev/null || true
  done

  rm -rf "$PROJECT_ROOT/store"
  rm -rf "$PROJECT_ROOT/dist"
  rm -rf "$PROJECT_ROOT/data/sessions"
  rm -rf "$PROJECT_ROOT/logs"
  ok "Cleaned"
fi

# ── Step 1: Install dependencies ────────────────────────────────────────────

info "Installing npm dependencies..."
npm ci --silent 2>&1 | tail -1 || fail "npm ci failed. Check logs."

# Verify native module
if ! node -e "require('better-sqlite3')" 2>/dev/null; then
  fail "better-sqlite3 native module failed to load. Check build tools."
fi
ok "Dependencies installed"

# ── Step 1b: Patch container runtime autodetection ─────────────────────────

CONTAINER_RUNTIME_TS="$PROJECT_ROOT/src/container-runtime.ts"
if [ -f "$CONTAINER_RUNTIME_TS" ]; then
  if [ "$RUNTIME" != "docker" ]; then
    info "Patching container-runtime.ts to use $RUNTIME..."
    sed "s/\"docker\"/\"$RUNTIME\"/g" "$CONTAINER_RUNTIME_TS" > "${CONTAINER_RUNTIME_TS}.tmp" \
      && mv "${CONTAINER_RUNTIME_TS}.tmp" "$CONTAINER_RUNTIME_TS"
    ok "Container runtime set to $RUNTIME in source"
  else
    ok "Container runtime already set to docker (default)"
  fi
else
  warn "src/container-runtime.ts not found — skipping runtime patch"
fi

# ── Step 2: Build TypeScript ────────────────────────────────────────────────

info "Building TypeScript..."
npm run build --silent 2>&1 || fail "TypeScript build failed."
ok "Built dist/"

# ── Step 3: Build container image ───────────────────────────────────────────

info "Building container image (this may take a few minutes on first run)..."
CONTAINER_RUNTIME="$RUNTIME" "$PROJECT_ROOT/container/build.sh" latest 2>&1 | tail -3
ok "Container image built: nanoclaw-agent:latest"

# ── Step 4: Test container ──────────────────────────────────────────────────

info "Testing container..."
CONTAINER_TIMEOUT=30
TEST_TMPFILE=$(mktemp)

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

echo '{"prompt":"Say hello","groupFolder":"test","chatJid":"test@init","isMain":false}' | \
  $RUNTIME run -i --rm "${CRED_ARGS[@]}" nanoclaw-agent:latest >"$TEST_TMPFILE" 2>/dev/null &
TEST_PID=$!
( sleep "$CONTAINER_TIMEOUT" && kill "$TEST_PID" 2>/dev/null ) &
WATCHDOG_PID=$!
wait "$TEST_PID" 2>/dev/null && TEST_EXIT=0 || TEST_EXIT=$?
kill "$WATCHDOG_PID" 2>/dev/null 2>&1 || true
wait "$WATCHDOG_PID" 2>/dev/null 2>&1 || true
TEST_OUTPUT=$(cat "$TEST_TMPFILE")
rm -f "$TEST_TMPFILE"
if echo "$TEST_OUTPUT" | grep -q "NANOCLAW_OUTPUT"; then
  ok "Container runs successfully"
elif [ "$TEST_EXIT" -eq 137 ] || [ -z "$TEST_OUTPUT" ]; then
  warn "Container test timed out after ${CONTAINER_TIMEOUT}s (check credentials and network)"
else
  warn "Container test didn't produce expected output (may need credentials)"
fi

# ── Step 5: Register main group ─────────────────────────────────────────────

info "Registering main group..."
mkdir -p "$PROJECT_ROOT/store" "$PROJECT_ROOT/data" "$PROJECT_ROOT/logs"
mkdir -p "$PROJECT_ROOT/groups/main/logs"

# Use the setup register step
npx tsx setup/index.ts --step register \
  --jid "main@init.local" \
  --name "Main" \
  --trigger "@${ASSISTANT_NAME}" \
  --folder "main" \
  --channel "cli" \
  --is-main \
  --no-trigger-required \
  --assistant-name "$ASSISTANT_NAME" 2>&1 | grep -v "^===" || true
ok "Main group registered"

# ── Step 5b: Write main group CLAUDE.md ─────────────────────────────────────

MAIN_CLAUDE_MD="$PROJECT_ROOT/groups/main/CLAUDE.md"
SOULS_DIR="$SCRIPT_DIR/souls"
HONORE_SOURCE="$SOULS_DIR/claude-honore.md"
if [ ! -f "$MAIN_CLAUDE_MD" ]; then
  if [ ! -f "$HONORE_SOURCE" ]; then
    fail "claude-honore.md not found at $HONORE_SOURCE"
  fi
  info "Writing main group CLAUDE.md from souls/claude-honore.md..."
  cp "$HONORE_SOURCE" "$MAIN_CLAUDE_MD"
  ok "Main group CLAUDE.md written (Honoré persona)"
else
  ok "Main group CLAUDE.md already exists (skipped)"
fi

# ── Step 5c: Copy agent soul files ────────────────────────────────────────

SOULS_DEST="$PROJECT_ROOT/groups/main/souls"
mkdir -p "$SOULS_DEST"

for soul in claude-vautrin.md claude-rastignac.md; do
  if [ -f "$SOULS_DIR/$soul" ]; then
    cp "$SOULS_DIR/$soul" "$SOULS_DEST/$soul"
  else
    warn "Soul file $soul not found in $SOULS_DIR"
  fi
done
ok "Agent souls deployed to groups/main/souls/ (Vautrin, Rastignac)"

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

# ── Step 7: Service setup ──────────────────────────────────────────────────

info "Setting up background service..."
npx tsx setup/index.ts --step service 2>&1 | grep -v "^===" || true
ok "Service configured"

# ── Done ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}NanoClaw initialized successfully.${NC}"
echo ""
echo "  Runtime:   $RUNTIME"
echo "  Assistant: $ASSISTANT_NAME"
echo "  Groups:    groups/main/"
echo "  Database:  store/messages.db"
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

  # Build mount args for main group
  GROUP_DIR="$PROJECT_ROOT/groups/main"
  SESSION_DIR="$PROJECT_ROOT/data/sessions/main"
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
