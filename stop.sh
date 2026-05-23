#!/bin/bash
# Stop N184: the host controller + the compose stack.
# Data under ./data/ is preserved (this does not remove it).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CTRL_PID="$ROOT/.controller.pid"
DOCKER_CFG="$ROOT/.n184-docker-empty"

info() { echo "▸ $*"; }
ok()   { echo "✓ $*"; }

# 1. controller (host process)
if [ -f "$CTRL_PID" ] && kill -0 "$(cat "$CTRL_PID")" 2>/dev/null; then
  info "Stopping controller (pid $(cat "$CTRL_PID"))..."
  kill "$(cat "$CTRL_PID")" 2>/dev/null || true
fi
rm -f "$CTRL_PID"
# Belt-and-suspenders: kill any stray controller python (a duplicate would
# fight over the Telegram token on the next start).
pkill -f "controller/.venv/bin/python main.py" 2>/dev/null || true
pkill -f "MacOS/Python main.py" 2>/dev/null || true
ok "controller stopped"

# 2. compose stack (containers + network removed; ./data preserved)
info "Stopping compose stack..."
mkdir -p "$DOCKER_CFG"; echo '{}' > "$DOCKER_CFG/config.json"
DOCKER_CONFIG="$DOCKER_CFG" podman compose down 2>&1 | tail -3 || true
ok "compose stack stopped (data in ./data preserved)"

echo
ok "N184 stopped. Restart with ./start.sh"
