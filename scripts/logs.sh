#!/usr/bin/env bash
# scripts/logs.sh — Tail logs from all (or a specific) AgentGuard service.
#
# Usage:
#   ./scripts/logs.sh                    # tail all services
#   ./scripts/logs.sh server             # backend only
#   ./scripts/logs.sh frontend           # web UI only
#   ./scripts/logs.sh server --tail=50

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/compose-common.sh"

agentguard_resolve_compose || { echo "docker compose not found" >&2; exit 1; }
agentguard_select_compose_files

exec "${AGENTGUARD_COMPOSE[@]}" "${AGENTGUARD_COMPOSE_FILES[@]}" logs -f "$@"
