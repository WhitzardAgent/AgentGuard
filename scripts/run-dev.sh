#!/usr/bin/env bash
# scripts/run-dev.sh — Native development launcher for the server PDP (no Docker).
#
# Mirrors the compose defaults as closely as possible for local development:
# - bootstraps .env from .env.example
# - creates rules/ if needed
# - uses the same host/port defaults as the container entrypoint
# - derives AGENTGUARD_MYSQL_URL from MYSQL_* against 127.0.0.1 for native MySQL
#
# Usage:
#   ./scripts/run-dev.sh            # start server on $AGENTGUARD_PORT (default 38080)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/native-env.sh"
native_env_bootstrap

if [ ! -d ".venv" ]; then
    echo "[run-dev] Creating virtual environment…"
    python -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

if ! python - <<'PY' >/dev/null 2>&1
import cryptography
import fastapi
import pydantic
import pymysql
import uvicorn
PY
then
    echo "[run-dev] Installing native server dependencies…"
    pip install --upgrade pip -q
    pip install "pydantic>=2.5,<3.0" "cryptography>=42" "fastapi>=0.110" "uvicorn>=0.27" "pymysql>=1.1" -q
fi

export PYTHONPATH="$ROOT/src/client/python:$ROOT/src:$ROOT/src/server:$ROOT"
HOST="$AGENTGUARD_HOST"
PORT="$AGENTGUARD_PORT"

echo "[run-dev] Starting AgentGuard server → http://localhost:${PORT}"
if [ "${AGENTGUARD_NATIVE_DERIVED_MYSQL_URL:-0}" = "1" ]; then
    echo "[run-dev] Derived native MySQL target → 127.0.0.1:${MYSQL_PORT:-3306}/${MYSQL_DATABASE:-agentguard}"
fi
exec uvicorn backend.api.app:app --host "$HOST" --port "$PORT" --reload
