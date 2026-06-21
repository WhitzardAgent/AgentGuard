#!/usr/bin/env bash
# scripts/run-dev.sh — Native development launcher for the server PDP (no Docker).
#
# Sets the PYTHONPATH for the monorepo layout, installs the server deps into a
# local venv, then runs the FastAPI app with uvicorn (auto-reload).
#
# Usage:
#   ./scripts/run-dev.sh            # start server on $AGENTGUARD_PORT (default 38080)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

[ -f .env ] && { set -a; . ./.env; set +a; }

if [ ! -d ".venv" ]; then
    echo "[run-dev] Creating virtual environment…"
    python -m venv .venv
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install "pydantic>=2.5,<3.0" "fastapi>=0.110" "uvicorn>=0.27" -q
fi
# shellcheck disable=SC1091
. .venv/bin/activate

export PYTHONPATH="$ROOT/src/client/python:$ROOT/src:$ROOT/src/server:$ROOT"
HOST="${AGENTGUARD_HOST:-0.0.0.0}"
PORT="${AGENTGUARD_PORT:-38080}"

echo "[run-dev] Starting AgentGuard server → http://localhost:${PORT}"
exec uvicorn backend.api.app:app --host "$HOST" --port "$PORT" --reload
