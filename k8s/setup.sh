#!/bin/bash
set -euo pipefail

# N184 Kubernetes Setup
# Installs k3s/k3d, KEDA, builds images, and deploys N184 to Kubernetes.
# Works on macOS (k3d) and Linux (k3s).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N184_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Options ───────────────────────────────────────────────────────────
# --encrypt-secrets   Enable encryption at rest for k8s Secrets (off by default)

ENCRYPT_SECRETS=false
for arg in "$@"; do
  case "$arg" in
    --encrypt-secrets) ENCRYPT_SECRETS=true ;;
  esac
done

# ── Colors ────────────────────────────────────────────────────────────

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

# ── Detect Platform ───────────────────────────────────────────────────

OS=$(uname -s)
OVERLAY="production"
CONTAINER_RUNTIME="docker"

echo ""
echo -e "${BOLD}N184 Kubernetes Setup${NC}"
echo ""

case "$OS" in
  Darwin)
    OVERLAY="local"
    info "Platform: macOS (will use k3d)"

    # Check for Docker
    if ! command -v docker >/dev/null 2>&1; then
      fail "Docker not found. Install Docker Desktop or Rancher Desktop first."
    fi

    # Install k3d if missing
    if ! command -v k3d >/dev/null 2>&1; then
      info "Installing k3d..."
      if command -v brew >/dev/null 2>&1; then
        brew install k3d
      else
        curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
      fi
      ok "k3d installed"
    else
      ok "k3d already installed"
    fi

    # Create k3d cluster with volume mounts
    if k3d cluster list 2>/dev/null | grep -q "n184"; then
      ok "k3d cluster 'n184' already exists"
    else
      info "Creating k3d cluster 'n184'..."
      mkdir -p "$HOME/.n184" "/tmp/n184-honore-sessions"

      K3D_ARGS=(
        --volume "$HOME/.n184:/tmp/n184-palace"
        --volume "/tmp/n184-honore-sessions:/tmp/n184-honore-sessions"
        --agents 0
        --wait
      )

      if [ "$ENCRYPT_SECRETS" = true ]; then
        K3D_ARGS+=(--k3s-arg "--secrets-encryption@server:0")
        info "Secrets encryption at rest: ENABLED"
      fi

      k3d cluster create n184 "${K3D_ARGS[@]}"
      ok "k3d cluster created"
    fi

    # Set kubeconfig
    export KUBECONFIG=$(k3d kubeconfig write n184)
    ok "KUBECONFIG set for k3d cluster"
    ;;

  Linux)
    OVERLAY="production"
    info "Platform: Linux (will use k3s)"

    # Install k3s if missing
    if ! command -v kubectl >/dev/null 2>&1; then
      info "Installing k3s..."
      K3S_ARGS=""
      if [ "$ENCRYPT_SECRETS" = true ]; then
        K3S_ARGS="--secrets-encryption"
        info "Secrets encryption at rest: ENABLED"
      fi
      curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="$K3S_ARGS" sh -
      ok "k3s installed"
    else
      ok "kubectl already available"
      # Enable encryption on existing cluster if requested
      if [ "$ENCRYPT_SECRETS" = true ]; then
        if ! k3s secrets-encrypt status 2>/dev/null | grep -q "Enabled"; then
          info "Enabling secrets encryption on existing cluster..."
          sudo k3s secrets-encrypt enable
          sudo systemctl restart k3s
          ok "Secrets encryption enabled (restart complete)"
        else
          ok "Secrets encryption already enabled"
        fi
      fi
    fi

    # Set kubeconfig for k3s
    if [ -f /etc/rancher/k3s/k3s.yaml ]; then
      export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
    fi
    ok "KUBECONFIG set"

    # Create palace directory
    mkdir -p "$HOME/.n184" "$HOME/.claude"
    ;;

  *)
    fail "Unsupported platform: $OS"
    ;;
esac

# Verify kubectl works
kubectl cluster-info >/dev/null 2>&1 || fail "kubectl cannot connect to cluster"
ok "Cluster is reachable"

# ── Install KEDA ──────────────────────────────────────────────────────

info "Installing KEDA..."

if ! command -v helm >/dev/null 2>&1; then
  info "Installing Helm..."
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  ok "Helm installed"
fi

helm repo add kedacore https://kedacore.github.io/charts 2>/dev/null || true
helm repo update >/dev/null 2>&1

if helm list -n keda 2>/dev/null | grep -q keda; then
  ok "KEDA already installed"
else
  helm upgrade --install keda kedacore/keda \
    --namespace keda --create-namespace \
    --wait --timeout 120s
  ok "KEDA installed"
fi

# ── Create Namespace ──────────────────────────────────────────────────

kubectl create namespace n184 2>/dev/null || true
ok "Namespace 'n184' ready"

# ── Label Node ────────────────────────────────────────────────────────

NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl label node "$NODE" n184.io/palace-node=true --overwrite >/dev/null
ok "Node '$NODE' labeled as palace node"

# ── Create Secrets ────────────────────────────────────────────────────

info "Configuring API secrets..."

# Check for .env file first
if [ -f "$N184_ROOT/.env" ]; then
  info "Reading from .env file..."
  set -a
  source "$N184_ROOT/.env" 2>/dev/null || true
  set +a
fi

# Prompt for required keys
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  read -sp "  Anthropic API Key: " ANTHROPIC_API_KEY
  echo
fi

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  read -sp "  Telegram Bot Token: " TELEGRAM_BOT_TOKEN
  echo
fi

# Optional multi-model keys (read from .env or leave empty)
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"

# Optional channel credentials
SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
SLACK_APP_TOKEN="${SLACK_APP_TOKEN:-}"
EMAIL_IMAP_HOST="${EMAIL_IMAP_HOST:-}"
EMAIL_IMAP_USER="${EMAIL_IMAP_USER:-}"
EMAIL_IMAP_PASS="${EMAIL_IMAP_PASS:-}"
EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST:-}"

# Show which providers are configured
echo ""
info "Model providers:"
[ -n "$OPENAI_API_KEY" ] && ok "  OpenAI" || warn "  OpenAI (not set)"
[ -n "$DEEPSEEK_API_KEY" ] && ok "  DeepSeek" || warn "  DeepSeek (not set)"
[ -n "$GEMINI_API_KEY" ] && ok "  Gemini" || warn "  Gemini (not set)"

info "Messaging channels:"
[ -n "$TELEGRAM_BOT_TOKEN" ] && ok "  Telegram" || warn "  Telegram (not set)"
[ -n "$SLACK_BOT_TOKEN" ] && ok "  Slack" || warn "  Slack (not set)"
[ -n "$EMAIL_IMAP_HOST" ] && ok "  Email (IMAP: $EMAIL_IMAP_HOST)" || warn "  Email (not set)"
echo ""

# TODO: Add support for local LLM servers (Ollama, llama.cpp, MLX).
# When implemented, read OLLAMA_BASE_URL / LLAMA_CPP_BASE_URL / MLX_BASE_URL
# from .env and pass as config to agent pods.

kubectl create secret generic n184-api-keys \
  --namespace n184 \
  --from-literal=ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}" \
  --from-literal=TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --from-literal=DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY}" \
  --from-literal=GEMINI_API_KEY="${GEMINI_API_KEY}" \
  --from-literal=SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN}" \
  --from-literal=SLACK_APP_TOKEN="${SLACK_APP_TOKEN}" \
  --from-literal=EMAIL_IMAP_HOST="${EMAIL_IMAP_HOST}" \
  --from-literal=EMAIL_IMAP_USER="${EMAIL_IMAP_USER}" \
  --from-literal=EMAIL_IMAP_PASS="${EMAIL_IMAP_PASS}" \
  --from-literal=EMAIL_SMTP_HOST="${EMAIL_SMTP_HOST}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null
ok "Secrets configured"

# ── Create Soul ConfigMaps ────────────────────────────────────────────

info "Creating soul ConfigMaps..."
for soul in honore vautrin rastignac bianchon; do
  SOUL_FILE="$N184_ROOT/souls/claude-${soul}.md"
  if [ -f "$SOUL_FILE" ]; then
    kubectl create configmap "n184-soul-${soul}" \
      --namespace n184 \
      --from-file="CLAUDE.md=${SOUL_FILE}" \
      --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  else
    warn "Soul file not found: $SOUL_FILE"
  fi
done
ok "Soul ConfigMaps created"

# ── Build Container Images ────────────────────────────────────────────

info "Building N184 agent image..."
cd "$N184_ROOT/container"
bash build.sh latest 2>&1 | tail -3
ok "Agent image built: n184-agent:latest"

info "Building N184 controller image..."
cd "$N184_ROOT/controller"
docker build -t n184-controller:latest . 2>&1 | tail -3
ok "Controller image built: n184-controller:latest"

# Import images to k3d if applicable
if [ "$OVERLAY" = "local" ]; then
  info "Importing images to k3d..."
  k3d image import n184-agent:latest n184-controller:latest -c n184
  ok "Images imported to k3d"
fi

# ── Apply Manifests ───────────────────────────────────────────────────

info "Applying k8s manifests (overlay: $OVERLAY)..."
kubectl apply -k "$SCRIPT_DIR/overlays/$OVERLAY"
ok "Manifests applied"

# ── Wait for Rollout ──────────────────────────────────────────────────

info "Waiting for pods to start..."
kubectl rollout status deployment/redis -n n184 --timeout=60s
kubectl rollout status deployment/chromadb -n n184 --timeout=120s
kubectl rollout status deployment/controller -n n184 --timeout=120s
kubectl rollout status deployment/honore -n n184 --timeout=180s

# ── Done ──────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}N184 deployed to Kubernetes!${NC}"
echo ""
echo "  Overlay:    $OVERLAY"
echo "  Namespace:  n184"
echo "  Palace:     ~/.n184/"
if [ "$ENCRYPT_SECRETS" = true ]; then
echo "  Encryption: secrets encrypted at rest"
else
echo "  Encryption: off (enable with --encrypt-secrets)"
fi
echo ""
echo "Quick commands:"
echo "  kubectl get pods -n n184              # Check pod status"
echo "  kubectl logs -f deploy/honore -n n184 # Watch Honore logs"
echo "  kubectl logs -f deploy/controller -n n184  # Watch controller"
echo "  kubectl get jobs -n n184              # List agent Jobs"
echo "  kubectl get scaledjob -n n184         # KEDA Vautrin status"
echo ""
kubectl get pods -n n184
