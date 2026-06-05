#!/usr/bin/env sh
# AgentGuard container entrypoint.
#
# Translates the documented AGENTGUARD_* env vars into `agentguard` CLI flags
# so docker-compose deployments can be configured purely via environment.
#
# Supported CMDs:
#   serve     (default) — start the AgentGuard runtime API server
#   frontend            — start the web UI (Python HTTP proxy on FRONTEND_PORT)
#   *                   — passed directly to the `agentguard` CLI

set -eu

CMD="${1:-serve}"
shift || true

# ── Frontend web UI ──────────────────────────────────────────────────────────
if [ "$CMD" = "frontend" ]; then
    exec python /opt/agentguard/frontend/app.py "$@"
fi

# ── Client-side Harness e2e (dual-path PEP against the server PDP) ────────────
if [ "$CMD" = "client" ]; then
    exec python -m agentguard.examples.remote_client_e2e "$@"
fi

# ── Pass-through for other agentguard sub-commands (check, validate, …) ──────
if [ "$CMD" != "serve" ]; then
    exec agentguard "$CMD" "$@"
fi

ARGS="--host ${AGENTGUARD_HOST:-0.0.0.0} --port ${AGENTGUARD_PORT:-38080}"
ARGS="$ARGS --mode ${AGENTGUARD_MODE:-enforce}"
ARGS="$ARGS --runtime-mode ${AGENTGUARD_RUNTIME_MODE:-sync}"
ARGS="$ARGS --log-level ${AGENTGUARD_LOG_LEVEL:-info}"

if [ -n "${AGENTGUARD_API_KEY:-}" ]; then
    ARGS="$ARGS --api-key $AGENTGUARD_API_KEY"
fi

if [ "${AGENTGUARD_NO_BUILTIN:-0}" = "1" ]; then
    ARGS="$ARGS --no-builtin"
fi

if [ -n "${AGENTGUARD_POLICY:-}" ]; then
    for path in $AGENTGUARD_POLICY; do
        ARGS="$ARGS --policy $path"
    done
fi

if [ -n "${AGENTGUARD_RULE_PACK_CONFIG:-}" ]; then
    ARGS="$ARGS --rule-pack-config $AGENTGUARD_RULE_PACK_CONFIG"
fi

if [ -n "${AGENTGUARD_STATE_CACHE:-}" ]; then
    ARGS="$ARGS --state-cache $AGENTGUARD_STATE_CACHE"
fi

if [ -n "${AGENTGUARD_POSTGRES_URL:-}" ]; then
    ARGS="$ARGS --postgres-url $AGENTGUARD_POSTGRES_URL"
fi

if [ "${AGENTGUARD_WATCH:-0}" = "1" ]; then
    ARGS="$ARGS --watch"
    ARGS="$ARGS --watch-interval ${AGENTGUARD_WATCH_INTERVAL:-5}"
fi

exec agentguard serve $ARGS "$@"
