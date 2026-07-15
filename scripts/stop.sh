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
warn() { :; }

# shellcheck disable=SC1091
. "$SCRIPT_DIR/compose-common.sh"

agentguard_resolve_compose || { echo "docker compose not found" >&2; exit 1; }
agentguard_select_compose_files

VOLUMES_FLAG=""
for arg in "$@"; do
    [ "$arg" = "--volumes" ] || [ "$arg" = "-v" ] && VOLUMES_FLAG="--volumes"
done

info "Stopping AgentGuard services…"
DOWN_ARGS=(down)
[ -n "$VOLUMES_FLAG" ] && DOWN_ARGS+=("$VOLUMES_FLAG")
"${AGENTGUARD_COMPOSE[@]}" "${AGENTGUARD_COMPOSE_FILES[@]}" "${DOWN_ARGS[@]}"

if [ -n "$VOLUMES_FLAG" ]; then
    info "Persistent volumes removed."
else
    info "Stopped. Data volumes preserved. Use --volumes to also remove them."
fi
