#!/usr/bin/env bash

agentguard_resolve_compose() {
    if docker compose version &>/dev/null 2>&1; then
        AGENTGUARD_COMPOSE=(docker compose)
    elif command -v docker-compose &>/dev/null; then
        AGENTGUARD_COMPOSE=(docker-compose)
    else
        return 1
    fi
}

agentguard_bootstrap_project() {
    if [ ! -f .env ]; then
        info ".env not found — copying .env.example → .env"
        cp .env.example .env
        warn "Review .env and set any required secrets (e.g. AGENTGUARD_LLM_API_KEY) before proceeding."
    fi

    if [ ! -d rules ]; then
        info "Creating empty rules/ directory (add your .rules files here)"
        mkdir -p rules
    fi
}

agentguard_load_env() {
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
}

agentguard_select_compose_files() {
    AGENTGUARD_COMPOSE_FILES=(-f docker-compose.yml)
    AGENTGUARD_LIVE_CODE_ENABLED=0

    if [ "${AGENTGUARD_DISABLE_LIVE_CODE:-0}" != "1" ] && [ -f docker-compose.live.yml ]; then
        AGENTGUARD_COMPOSE_FILES+=(-f docker-compose.live.yml)
        AGENTGUARD_LIVE_CODE_ENABLED=1
    fi
}

agentguard_prepare_build_state() {
    AGENTGUARD_STATE_DIR=".codex"
    AGENTGUARD_IMAGE_STAMP_FILE="$AGENTGUARD_STATE_DIR/agentguard-image.sha256"
    mkdir -p "$AGENTGUARD_STATE_DIR"
}

agentguard_hash_build_inputs() {
    if command -v sha256sum &>/dev/null; then
        sha256sum Dockerfile pyproject.toml | sha256sum | awk '{print $1}'
    elif command -v shasum &>/dev/null; then
        shasum -a 256 Dockerfile pyproject.toml | shasum -a 256 | awk '{print $1}'
    else
        return 1
    fi
}

agentguard_build_inputs_changed() {
    local current_stamp previous_stamp

    current_stamp="$(agentguard_hash_build_inputs)"
    previous_stamp=""
    [ -f "$AGENTGUARD_IMAGE_STAMP_FILE" ] && previous_stamp="$(cat "$AGENTGUARD_IMAGE_STAMP_FILE")"
    [ "$current_stamp" != "$previous_stamp" ]
}

agentguard_persist_build_stamp() {
    agentguard_hash_build_inputs > "$AGENTGUARD_IMAGE_STAMP_FILE"
}
