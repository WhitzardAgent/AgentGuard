#!/usr/bin/env sh
# AgentGuard container entrypoint.
#
# Supported CMDs:
#   serve    (default) — start the server PDP (FastAPI via uvicorn)
#   frontend           — start the management console web UI (proxies to the server)
set -eu

CMD="${1:-serve}"
shift || true

HOST="${AGENTGUARD_HOST:-0.0.0.0}"
PORT="${AGENTGUARD_PORT:-38080}"

case "$CMD" in
  serve)
    exec uvicorn backend.api.app:app --host "$HOST" --port "$PORT"
    ;;
  frontend)
    export FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
    export FRONTEND_PORT="${FRONTEND_PORT:-38008}"
    exec python src/server/frontend/app.py
    ;;
  *)
    echo "unsupported command for server image: $CMD" >&2
    exit 2
    ;;
esac
