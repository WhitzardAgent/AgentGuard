#!/usr/bin/env bash
# scripts/start.sh — One-click Docker Compose startup for AgentGuard.
#
# Usage:
#   ./scripts/start.sh          # build images if needed, start all services
#   ./scripts/start.sh --build  # force rebuild images
#   ./scripts/start.sh -d       # start in background (detached)
#   BUILD=1 ./scripts/start.sh  # alias for --build

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# ── ANSI colours ─────────────────────────────────────────────────────────────
_bold='\033[1m'
_green='\033[0;32m'
_yellow='\033[0;33m'
_red='\033[0;31m'
_reset='\033[0m'

info()  { echo -e "${_green}[agentguard]${_reset} $*"; }
warn()  { echo -e "${_yellow}[warn]${_reset} $*"; }
error() { echo -e "${_red}[error]${_reset} $*" >&2; exit 1; }

# ── Parse arguments ───────────────────────────────────────────────────────────
BUILD_FLAG=""
DETACH_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --build|-b) BUILD_FLAG="--build" ;;
        -d|--detach) DETACH_FLAG="-d" ;;
    esac
done
[ "${BUILD:-0}" = "1" ] && BUILD_FLAG="--build"

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    error "Docker is not installed or not in PATH. Install Docker Desktop / Docker Engine first."
fi

# Support both 'docker compose' (v2 plugin) and legacy 'docker-compose'
if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    error "Docker Compose is not available. Install the Docker Compose plugin or docker-compose."
fi

# ── Bootstrap .env ────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    info ".env not found — copying .env.example → .env"
    cp .env.example .env
    warn "Review .env and set any required secrets (e.g. AGENTGUARD_LLM_API_KEY) before proceeding."
fi

# ── Ensure rules directory exists ─────────────────────────────────────────────
if [ ! -d rules ]; then
    info "Creating empty rules/ directory (add your .rules files here)"
    mkdir -p rules
fi

# ── Load .env for local variable substitution in this script ──────────────────
set -a
# shellcheck disable=SC1091
. ./.env
set +a

AGENTGUARD_PORT="${AGENTGUARD_PORT:-38080}"
AGENTGUARD_FRONTEND_PORT="${AGENTGUARD_FRONTEND_PORT:-38008}"

# ── Start services ────────────────────────────────────────────────────────────
info "Starting AgentGuard stack (this may take a moment on first run)…"

# If no images exist yet, force a build regardless of flags.
if ! docker image inspect agentguard:latest &>/dev/null; then
    BUILD_FLAG="--build"
fi

# shellcheck disable=SC2086
$COMPOSE up $BUILD_FLAG $DETACH_FLAG

if [ -n "$DETACH_FLAG" ]; then
    echo ""
    echo -e "${_bold}AgentGuard is running:${_reset}"
    echo -e "  Runtime API  →  ${_green}http://localhost:${AGENTGUARD_PORT}${_reset}"
    echo -e "  Web UI       →  ${_green}http://localhost:${AGENTGUARD_FRONTEND_PORT}${_reset}"
    echo ""
    echo "  Logs:   ./scripts/logs.sh"
    echo "  Stop:   ./scripts/stop.sh"
fi
