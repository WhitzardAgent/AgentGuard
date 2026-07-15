#!/usr/bin/env bash
# scripts/start.sh — One-click Docker Compose startup for AgentGuard.
#
# Usage:
#   ./scripts/start.sh          # start all services using the latest repo code
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

# shellcheck disable=SC1091
. "$SCRIPT_DIR/compose-common.sh"

BUILD_FLAG=""
DETACH_FLAG=""
for arg in "$@"; do
    case "$arg" in
        --build|-b) BUILD_FLAG="--build" ;;
        -d|--detach) DETACH_FLAG="-d" ;;
    esac
done
[ "${BUILD:-0}" = "1" ] && BUILD_FLAG="--build"

if ! command -v docker &>/dev/null; then
    error "Docker is not installed or not in PATH. Install Docker Desktop / Docker Engine first."
fi

agentguard_resolve_compose || error "Docker Compose is not available. Install the Docker Compose plugin or docker-compose."
agentguard_bootstrap_project
agentguard_load_env
agentguard_select_compose_files
agentguard_prepare_build_state

AGENTGUARD_PORT="${AGENTGUARD_PORT:-38080}"
AGENTGUARD_FRONTEND_PORT="${AGENTGUARD_FRONTEND_PORT:-38008}"

info "Starting AgentGuard stack (this may take a moment on first run)…"

if ! docker image inspect agentguard:latest &>/dev/null; then
    BUILD_FLAG="--build"
    info "agentguard:latest not found locally — enabling build."
elif [ -n "$BUILD_FLAG" ]; then
    info "Forced rebuild requested."
elif agentguard_build_inputs_changed; then
    BUILD_FLAG="--build"
    info "Docker build inputs changed — enabling rebuild."
fi

UP_ARGS=(up)
[ -n "$BUILD_FLAG" ] && UP_ARGS+=("$BUILD_FLAG")
[ -n "$DETACH_FLAG" ] && UP_ARGS+=("$DETACH_FLAG")
SERVICE_ARGS=()

if [ "$AGENTGUARD_LIVE_CODE_ENABLED" = "1" ]; then
    info "Using bind-mounted repo code for server/frontend."
    UP_ARGS+=(--force-recreate)
    SERVICE_ARGS=(server frontend)
fi

"${AGENTGUARD_COMPOSE[@]}" "${AGENTGUARD_COMPOSE_FILES[@]}" "${UP_ARGS[@]}" "${SERVICE_ARGS[@]}"
agentguard_persist_build_stamp

if [ -n "$DETACH_FLAG" ]; then
    echo ""
    echo -e "${_bold}AgentGuard is running:${_reset}"
    echo -e "  Runtime API  →  ${_green}http://localhost:${AGENTGUARD_PORT}${_reset}"
    echo -e "  Web UI       →  ${_green}http://localhost:${AGENTGUARD_FRONTEND_PORT}${_reset}"
    echo ""
    if [ "$AGENTGUARD_LIVE_CODE_ENABLED" = "1" ]; then
        echo "  Server/frontend source is bind-mounted from this repo."
        echo "  Rerun ./scripts/start.sh after code changes to reload containers."
    fi
    echo "  Logs:   ./scripts/logs.sh"
    echo "  Stop:   ./scripts/stop.sh"
fi
