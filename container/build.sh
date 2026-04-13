#!/bin/bash
# Build the N184 agent container image
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N184_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="n184-agent"
TAG="${1:-latest}"
CONTAINER_RUNTIME="${CONTAINER_RUNTIME:-docker}"

# ── Stage N184 Memory Palace files into build context ───────────────────
N184_STAGE="$SCRIPT_DIR/n184_memory_palace"

if [ -d "$N184_ROOT/n184" ] && [ -f "$N184_ROOT/n184/palace.py" ]; then
  rm -rf "$N184_STAGE"
  mkdir -p "$N184_STAGE"
  cp "$N184_ROOT/n184"/*.py "$N184_STAGE/"
  echo "Staged N184 Memory Palace package"
else
  echo "Warning: N184 package not found at $N184_ROOT/n184 — skipping"
fi

# Stage CLI wrapper
if [ -f "$N184_ROOT/n184_palace_cli.py" ]; then
  cp "$N184_ROOT/n184_palace_cli.py" "$SCRIPT_DIR/n184_palace_cli.py"
  echo "Staged n184_palace_cli.py"
fi

# Stage agent-runner
if [ -d "$N184_ROOT/agent-runner" ]; then
  rm -rf "$SCRIPT_DIR/agent-runner"
  mkdir -p "$SCRIPT_DIR/agent-runner"
  cp "$N184_ROOT/agent-runner/package"*.json "$SCRIPT_DIR/agent-runner/"
  cp "$N184_ROOT/agent-runner/tsconfig.json" "$SCRIPT_DIR/agent-runner/"
  cp -r "$N184_ROOT/agent-runner/src" "$SCRIPT_DIR/agent-runner/src"
  echo "Staged agent-runner"
fi

# ── Build ───────────────────────────────────────────────────────────────

echo "Building N184 agent container image..."
echo "Image: ${IMAGE_NAME}:${TAG}"

cd "$SCRIPT_DIR"
${CONTAINER_RUNTIME} build -t "${IMAGE_NAME}:${TAG}" .

# ── Import to k3d if available ──────────────────────────────────────────

if command -v k3d >/dev/null 2>&1; then
  if k3d cluster list 2>/dev/null | grep -q "n184"; then
    echo "Importing image to k3d cluster 'n184'..."
    k3d image import "${IMAGE_NAME}:${TAG}" -c n184
  fi
fi

# ── Clean up staged files ───────────────────────────────────────────────

rm -rf "$N184_STAGE" "$SCRIPT_DIR/n184_palace_cli.py" "$SCRIPT_DIR/agent-runner" 2>/dev/null || true

echo ""
echo "Build complete: ${IMAGE_NAME}:${TAG}"
