#!/usr/bin/env bash
# Generate and optionally apply the deployment-side files needed to connect a
# local Dify instance to AgentGuard without modifying Dify source code.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTGUARD_ROOT="$(dirname "$SCRIPT_DIR")"

DIFY_DIR=""
APP_IDS=""
NODE_IDS=""
SERVER_URL="http://host.docker.internal:38080"
CONSOLE_URL=""
API_KEY=""
POLICY=""
BOOTSTRAP_DIR=""
OUTPUT_FILE=""
APPLY="false"
AGENT_CHAT="true"

usage() {
    cat <<'EOF'
Usage:
  scripts/setup-dify-agentguard.sh \
    --dify-dir /path/to/dify \
    [--app-id <dify_app_id>] \
    [--server-url <agentguard_server_url>] \
    [--api-key <agentguard_api_key>] \
    [--policy <mounted_rules_path>] \
    [--console-url <agentguard_console_url>] \
    [--apply]

Options:
  --dify-dir       Dify source directory. The script writes into <dify-dir>/docker.
  --app-id         Optional Dify app id to guard. Repeat or pass comma-separated values.
                   Omit to guard all Dify apps in the api/worker process.
  --node-id        Legacy/debug filter for old Workflow Agent node paths.
                   Workflow/chatflow node coverage no longer requires node ids.
  --agent-chat     Enable the legacy Dify agent-chat adapter. This is enabled
                   by default so one setup covers Agent Chat and Workflow/Chatflow.
  --server-url     AgentGuard server API URL reachable from Dify containers.
                   Defaults to http://host.docker.internal:38080.
  --api-key        Optional AgentGuard API key.
  --policy         Optional AgentGuard policy name or mounted rules path.
  --console-url    Optional AgentGuard frontend URL to print after setup.
  --bootstrap-dir  Optional bootstrap directory. Defaults to <dify-dir>/agentguard-dify-bootstrap.
  --output-file    Optional compose override path. Defaults to <dify-dir>/docker/docker-compose.agentguard.yml.
  --apply          Run docker compose to recreate api/worker after generating files.
  -h, --help       Show this help.
EOF
}

append_csv() {
    local current="$1"
    local next="$2"
    if [ -z "$current" ]; then
        printf '%s' "$next"
    else
        printf '%s,%s' "$current" "$next"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --dify-dir)
            DIFY_DIR="${2:-}"
            shift 2
            ;;
        --app-id|--app-ids)
            APP_IDS="$(append_csv "$APP_IDS" "${2:-}")"
            shift 2
            ;;
        --node-id|--node-ids)
            NODE_IDS="$(append_csv "$NODE_IDS" "${2:-}")"
            shift 2
            ;;
        --server-url)
            SERVER_URL="${2:-}"
            shift 2
            ;;
        --console-url)
            CONSOLE_URL="${2:-}"
            shift 2
            ;;
        --api-key)
            API_KEY="${2:-}"
            shift 2
            ;;
        --policy)
            POLICY="${2:-}"
            shift 2
            ;;
        --bootstrap-dir)
            BOOTSTRAP_DIR="${2:-}"
            shift 2
            ;;
        --output-file)
            OUTPUT_FILE="${2:-}"
            shift 2
            ;;
        --apply)
            APPLY="true"
            shift
            ;;
        --agent-chat)
            AGENT_CHAT="true"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$DIFY_DIR" ]; then
    usage >&2
    exit 2
fi

DIFY_DIR="$(cd "$DIFY_DIR" && pwd)"
DIFY_DOCKER_DIR="$DIFY_DIR/docker"
if [ ! -d "$DIFY_DOCKER_DIR" ]; then
    echo "Dify docker directory not found: $DIFY_DOCKER_DIR" >&2
    exit 1
fi

BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-$DIFY_DIR/agentguard-dify-bootstrap}"
OUTPUT_FILE="${OUTPUT_FILE:-$DIFY_DOCKER_DIR/docker-compose.agentguard.yml}"

mkdir -p "$BOOTSTRAP_DIR"
cat > "$BOOTSTRAP_DIR/sitecustomize.py" <<'PY'
import logging

logger = logging.getLogger("agentguard.dify")

try:
    from agentguard.adapters.agent.dify_agent_chat import install_dify_agent_chat_adapter

    status = install_dify_agent_chat_adapter()
    logger.warning("AgentGuard Dify Agent Chat adapter status: %s", status)
except Exception:
    logger.exception("AgentGuard Dify Agent Chat adapter installation failed")

try:
    from agentguard.adapters.agent.dify import install_dify_adapter

    status = install_dify_adapter()
    logger.warning("AgentGuard Dify workflow adapter status: %s", status)
except Exception:
    logger.exception("AgentGuard Dify workflow adapter installation failed")
PY

AGENT_CHAT_ENABLED="$AGENT_CHAT"

cat > "$OUTPUT_FILE" <<YAML
services:
  api:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_DIFY_AGENT_CHAT_ENABLED: "$AGENT_CHAT_ENABLED"
      AGENTGUARD_SERVER_URL: "$SERVER_URL"
      AGENTGUARD_API_KEY: "$API_KEY"
      AGENTGUARD_POLICY: "$POLICY"
      AGENTGUARD_DIFY_APP_IDS: "$APP_IDS"
      AGENTGUARD_DIFY_NODE_IDS: "$NODE_IDS"
      AGENTGUARD_ENVIRONMENT: "dify"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - $AGENTGUARD_ROOT:/agentguard:ro
      - $BOOTSTRAP_DIR:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"

  worker:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_DIFY_AGENT_CHAT_ENABLED: "$AGENT_CHAT_ENABLED"
      AGENTGUARD_SERVER_URL: "$SERVER_URL"
      AGENTGUARD_API_KEY: "$API_KEY"
      AGENTGUARD_POLICY: "$POLICY"
      AGENTGUARD_DIFY_APP_IDS: "$APP_IDS"
      AGENTGUARD_DIFY_NODE_IDS: "$NODE_IDS"
      AGENTGUARD_ENVIRONMENT: "dify"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - $AGENTGUARD_ROOT:/agentguard:ro
      - $BOOTSTRAP_DIR:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
YAML

cat <<EOF
Generated:
  $BOOTSTRAP_DIR/sitecustomize.py
  $OUTPUT_FILE

Start Dify with AgentGuard:
  cd $DIFY_DOCKER_DIR
  docker compose -f docker-compose.yaml -f $(basename "$OUTPUT_FILE") up -d --force-recreate api worker
  docker compose -f docker-compose.yaml -f $(basename "$OUTPUT_FILE") restart nginx

EOF

if [ "$APPLY" = "true" ]; then
    (
        cd "$DIFY_DOCKER_DIR"
        docker compose -f docker-compose.yaml -f "$(basename "$OUTPUT_FILE")" up -d --force-recreate api worker
        docker compose -f docker-compose.yaml -f "$(basename "$OUTPUT_FILE")" restart nginx
    )
fi

if [ -n "$CONSOLE_URL" ]; then
    cat <<EOF
Open your AgentGuard console to configure rules and inspect Dify agents:
  $CONSOLE_URL
EOF
else
    cat <<'EOF'
Open the AgentGuard console provided by your AgentGuard server operator to configure rules and inspect Dify agents.
EOF
fi
