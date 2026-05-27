#!/usr/bin/env bash
# scripts/run-dev.sh — Native development launcher (no Docker required).
#
# Reads .env if present (same file used by docker compose), creates / activates
# a venv, installs dependencies, then starts the AgentGuard runtime API and
# (optionally) the web-UI frontend in parallel.
#
# Usage:
#   ./scripts/run-dev.sh               # backend + frontend
#   ./scripts/run-dev.sh --no-frontend # backend only
#   ./scripts/run-dev.sh --backend-only # alias

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

# ── Parse flags ───────────────────────────────────────────────────────────────
LAUNCH_FRONTEND=1
for arg in "$@"; do
    case "$arg" in
        --no-frontend|--backend-only) LAUNCH_FRONTEND=0 ;;
    esac
done

# ── Load .env ─────────────────────────────────────────────────────────────────
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

# ── Venv setup ────────────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "[run-dev] Creating virtual environment…"
    python -m venv .venv
    .venv/bin/pip install --upgrade pip -q
    .venv/bin/pip install -e ".[server,redis,postgres,dynamic]" -q
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# ── Build agentguard serve arguments ─────────────────────────────────────────
ARGS="--host ${AGENTGUARD_HOST:-0.0.0.0} --port ${AGENTGUARD_PORT:-38080}"
ARGS="$ARGS --mode ${AGENTGUARD_MODE:-enforce}"
ARGS="$ARGS --runtime-mode ${AGENTGUARD_RUNTIME_MODE:-sync}"
ARGS="$ARGS --log-level ${AGENTGUARD_LOG_LEVEL:-info}"

[ -n "${AGENTGUARD_API_KEY:-}" ]   && ARGS="$ARGS --api-key $AGENTGUARD_API_KEY"
[ "${AGENTGUARD_NO_BUILTIN:-0}" = "1" ] && ARGS="$ARGS --no-builtin"

if [ -n "${AGENTGUARD_POLICY:-}" ]; then
    for p in $AGENTGUARD_POLICY; do
        ARGS="$ARGS --policy $p"
    done
fi

[ -n "${AGENTGUARD_RULE_PACK_CONFIG:-}" ] && ARGS="$ARGS --rule-pack-config $AGENTGUARD_RULE_PACK_CONFIG"
[ -n "${AGENTGUARD_STATE_CACHE:-}" ]      && ARGS="$ARGS --state-cache $AGENTGUARD_STATE_CACHE"
[ -n "${AGENTGUARD_POSTGRES_URL:-}" ]     && ARGS="$ARGS --postgres-url $AGENTGUARD_POSTGRES_URL"
[ "${AGENTGUARD_WATCH:-0}" = "1" ]        && ARGS="$ARGS --watch --watch-interval ${AGENTGUARD_WATCH_INTERVAL:-5}"

# ── Start backend ─────────────────────────────────────────────────────────────
AGENTGUARD_PORT="${AGENTGUARD_PORT:-38080}"
FRONTEND_PORT="${FRONTEND_PORT:-8008}"

if [ "$LAUNCH_FRONTEND" = "1" ]; then
    # Run backend in background, frontend in foreground; kill both on exit.
    cleanup() {
        echo ""
        echo "[run-dev] Stopping all services…"
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    }
    trap cleanup EXIT INT TERM

    echo "[run-dev] Starting AgentGuard runtime  → http://localhost:${AGENTGUARD_PORT}"
    # shellcheck disable=SC2086
    python -m agentguard serve $ARGS "$@" &
    BACKEND_PID=$!

    # Brief pause so the backend can print its startup banner first
    sleep 1

    echo "[run-dev] Starting frontend web UI     → http://localhost:${FRONTEND_PORT}"
    FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}" \
    FRONTEND_PORT="$FRONTEND_PORT" \
    AGENTGUARD_API_BASE="${AGENTGUARD_API_BASE:-http://127.0.0.1:${AGENTGUARD_PORT}}" \
        python frontend/app.py
else
    echo "[run-dev] Starting AgentGuard runtime  → http://localhost:${AGENTGUARD_PORT}"
    # shellcheck disable=SC2086
    exec python -m agentguard serve $ARGS "$@"
fi
