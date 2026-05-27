#!/usr/bin/env bash
# scripts/logs.sh — Tail logs from all (or a specific) AgentGuard service.
#
# Usage:
#   ./scripts/logs.sh                    # tail all services
#   ./scripts/logs.sh agentguard         # backend only
#   ./scripts/logs.sh frontend           # web UI only
#   ./scripts/logs.sh agentguard --tail=50

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo "docker compose not found" >&2; exit 1
fi

# shellcheck disable=SC2086
exec $COMPOSE logs -f "$@"
