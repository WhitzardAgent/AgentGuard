#!/usr/bin/env sh
# AgentGuard container entrypoint.
#
# Supported CMDs:
#   serve   (default) — start the server PDP (FastAPI via uvicorn)
#   frontend          — start the management console web UI (proxies to the server)
#   client            — run the AgentDoG paired e2e example against $AGENTGUARD_SERVER_URL
#   example <name>    — run examples/<name>.py
#   *                 — passed through to the `python -m agentguard.cli` CLI
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
  client)
    exec python examples/remote_client_e2e.py "$@"
    ;;
  example)
    if [ "$#" -lt 1 ]; then
      echo "usage: example <name>" >&2
      exit 2
    fi
    exec python examples/"$1".py
    ;;
  *)
    exec python -m agentguard.cli "$CMD" "$@"
    ;;
esac
