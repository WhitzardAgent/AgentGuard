#!/usr/bin/env bash
# scripts/run-frontend.sh — Native development launcher for the management UI.
#
# Usage:
#   ./scripts/run-frontend.sh      # start frontend on $FRONTEND_PORT (default 8008)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[ -f .env ] && { set -a; . ./.env; set +a; }

HOST="${FRONTEND_HOST:-127.0.0.1}"
PORT="${FRONTEND_PORT:-8008}"

echo "[run-frontend] Starting AgentGuard UI -> http://${HOST}:${PORT}"
exec python src/server/frontend/app.py
