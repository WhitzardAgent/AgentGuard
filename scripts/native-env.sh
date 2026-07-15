#!/usr/bin/env bash
# Shared native-development environment bootstrap for AgentGuard scripts.

native_env_bootstrap() {
    local script_dir root mysql_user mysql_password mysql_database mysql_port

    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    root="$(dirname "$script_dir")"
    cd "$root"

    if [ ! -f .env ]; then
        echo "[native-env] .env not found — copying .env.example -> .env"
        cp .env.example .env
        echo "[native-env] Review .env and set any required secrets if needed."
    fi

    if [ ! -d rules ]; then
        echo "[native-env] Creating empty rules/ directory"
        mkdir -p rules
    fi

    [ -f .env ] && { set -a; . ./.env; set +a; }

    export AGENTGUARD_HOST="${AGENTGUARD_HOST:-0.0.0.0}"
    export AGENTGUARD_PORT="${AGENTGUARD_PORT:-38080}"
    export AGENTGUARD_FRONTEND_PORT="${AGENTGUARD_FRONTEND_PORT:-38008}"
    export FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
    export FRONTEND_PORT="${FRONTEND_PORT:-$AGENTGUARD_FRONTEND_PORT}"
    export AGENTGUARD_API_KEY="${AGENTGUARD_API_KEY:-sk-agentguard-backend-X9m42Vq7Tz8nL3pA6cR0yH5uJ1sWfKdE}"
    export AGENTGUARD_API_BASE="${AGENTGUARD_API_BASE:-http://127.0.0.1:${AGENTGUARD_PORT}}"

    if [ -z "${AGENTGUARD_MYSQL_URL:-}" ]; then
        mysql_user="${MYSQL_USER:-agentguard}"
        mysql_password="${MYSQL_PASSWORD:-agentguard}"
        mysql_database="${MYSQL_DATABASE:-agentguard}"
        mysql_port="${MYSQL_PORT:-3306}"
        export AGENTGUARD_MYSQL_URL="mysql://${mysql_user}:${mysql_password}@127.0.0.1:${mysql_port}/${mysql_database}"
        export AGENTGUARD_NATIVE_DERIVED_MYSQL_URL="1"
    fi
}
