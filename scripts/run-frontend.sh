#!/usr/bin/env bash
# scripts/run-frontend.sh — Native development launcher for the management UI.
#
# Usage:
#   ./scripts/run-frontend.sh      # start frontend on $FRONTEND_PORT (default 38008)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/native-env.sh"
native_env_bootstrap

HOST="$FRONTEND_HOST"
PORT="$FRONTEND_PORT"

echo "[run-frontend] Starting AgentGuard UI -> http://${HOST}:${PORT}"
echo "[run-frontend] Proxying API -> ${AGENTGUARD_API_BASE}"
exec python src/server/frontend/app.py
