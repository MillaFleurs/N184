#!/bin/bash
# Start N184 (podman + compose).
#
# Brings up the two layers:
#   1. compose stack (in podman):  Redis + ChromaDB + Honoré
#   2. controller (on the host):   Telegram bridge + sub-agent spawner
#
# Replaces the old init.sh (the abandoned NanoClaw path). Idempotent — safe to
# re-run; it won't start a second controller (a duplicate fights over Telegram
# getUpdates and both fail with a Conflict).
#
#   ./start.sh            # start everything (build image only if missing)
#   ./start.sh --build    # force a fresh agent-image build first
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

IMAGE="localhost/n184-agent:latest"
LOG_DIR="$ROOT/logs"
CTRL_LOG="$LOG_DIR/controller.log"
CTRL_PID="$ROOT/.controller.pid"
# Empty Docker config so `podman compose` (which shells out to docker-compose)
# doesn't choke on the osxkeychain credential helper for anonymous public pulls.
DOCKER_CFG="$ROOT/.n184-docker-empty"

info() { echo "▸ $*"; }
ok()   { echo "✓ $*"; }
warn() { echo "⚠ $*"; }
fail() { echo "✗ $*" >&2; exit 1; }

FORCE_BUILD=false
[ "${1:-}" = "--build" ] && FORCE_BUILD=true

[ -f "$ROOT/.env" ] || fail "No .env found. Copy .env.example to .env and fill in CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) + TELEGRAM_BOT_TOKEN."
mkdir -p "$LOG_DIR"
mkdir -p "$DOCKER_CFG"; echo '{}' > "$DOCKER_CFG/config.json"
# Honoré's host-visible data dir (replaces the old ./nanoclaw). Persists across
# `compose down` and `podman system reset`; back it up by copying ./data.
mkdir -p "$ROOT/data/palace" "$ROOT/data/sessions" "$ROOT/data/chroma" "$ROOT/data/redis"

# A leftover NanoClaw launchd job would poll the same Telegram token and cause a
# Conflict. Warn (don't auto-disable the user's services).
if launchctl list 2>/dev/null | grep -q nanoclaw; then
  warn "An old NanoClaw launchd job is running — it will fight over the Telegram token."
  warn "Stop it: launchctl bootout gui/\$(id -u)/com.nanoclaw  (and any com.nanoclaw-v2-*)"
fi

# 1. podman machine
if ! podman info >/dev/null 2>&1; then
  info "Starting podman machine..."
  podman machine start >/dev/null || fail "podman machine failed to start"
fi
ok "podman reachable"

# 2. agent image
if [ "$FORCE_BUILD" = true ] || ! podman image exists "$IMAGE"; then
  info "Building agent image ($IMAGE)..."
  CONTAINER_RUNTIME=podman bash "$ROOT/container/build.sh" latest
fi
ok "agent image present"

# 3. compose stack (Honoré + Redis + ChromaDB)
info "Starting compose stack..."
DOCKER_CONFIG="$DOCKER_CFG" podman compose up -d
ok "compose stack up (redis, chromadb, honoré)"

# 4. controller venv (python3.12 — 3.14 breaks python-telegram-bot's httpx stack)
if [ ! -x "$ROOT/controller/.venv/bin/python" ]; then
  command -v python3.12 >/dev/null 2>&1 || fail "python3.12 not found (brew install python@3.12) — needed for the controller."
  info "Creating controller venv (python3.12)..."
  python3.12 -m venv "$ROOT/controller/.venv"
  "$ROOT/controller/.venv/bin/pip" install -q --upgrade pip
  "$ROOT/controller/.venv/bin/pip" install -q "python-telegram-bot[webhooks]==21.*" "redis[hiredis]==5.*" "PyYAML==6.*"
  ok "controller venv ready"
fi

# 5. controller (exactly one — host process, Telegram bridge + sub-agent spawner)
if [ -f "$CTRL_PID" ] && kill -0 "$(cat "$CTRL_PID")" 2>/dev/null; then
  ok "controller already running (pid $(cat "$CTRL_PID")) — leaving it"
else
  rm -f "$CTRL_PID"
  info "Starting controller..."
  set -a; source "$ROOT/.env" 2>/dev/null || true; set +a
  export REDIS_URL="redis://localhost:6379"
  ( cd "$ROOT/controller" && nohup .venv/bin/python main.py > "$CTRL_LOG" 2>&1 & echo $! > "$CTRL_PID" )
  ok "controller started (pid $(cat "$CTRL_PID"))"
fi

echo
ok "N184 is up. Honoré should be live on your Telegram bot."
echo "    Honoré logs:     podman logs -f n184-honore-1"
echo "    Controller logs: tail -f $CTRL_LOG"
echo "    Stop everything: ./stop.sh"
