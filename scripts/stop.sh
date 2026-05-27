#!/usr/bin/env bash
# scripts/stop.sh — Stop (and optionally remove) the AgentGuard Docker stack.
#
# Usage:
#   ./scripts/stop.sh           # stop containers, keep volumes
#   ./scripts/stop.sh --volumes # stop containers AND remove persistent volumes

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

_green='\033[0;32m'
_reset='\033[0m'
info() { echo -e "${_green}[agentguard]${_reset} $*"; }

if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo "docker compose not found" >&2; exit 1
fi

VOLUMES_FLAG=""
for arg in "$@"; do
    [ "$arg" = "--volumes" ] || [ "$arg" = "-v" ] && VOLUMES_FLAG="--volumes"
done

info "Stopping AgentGuard services…"
# shellcheck disable=SC2086
$COMPOSE down $VOLUMES_FLAG

if [ -n "$VOLUMES_FLAG" ]; then
    info "Persistent volumes removed."
else
    info "Stopped. Data volumes preserved. Use --volumes to also remove them."
fi
