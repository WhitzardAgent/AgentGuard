#!/usr/bin/env bash
# Generate the deployment-side files needed to connect a local Dify Workflow
# Agent node to AgentGuard without modifying Dify source code.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTGUARD_ROOT="$(dirname "$SCRIPT_DIR")"

DIFY_DIR=""
APP_IDS=""
NODE_IDS=""
SERVER_URL=""
CONSOLE_URL=""
API_KEY=""
POLICY="dify_default"
BOOTSTRAP_DIR=""
OUTPUT_FILE=""

usage() {
    cat <<'EOF'
Usage:
  scripts/setup-dify-agentguard.sh \
    --dify-dir /path/to/dify \
    --app-id <dify_app_id> \
    --node-id <agent_node_id> \
    --server-url <agentguard_server_url> \
    [--api-key <agentguard_api_key>] \
    [--policy dify_default] \
    [--console-url <agentguard_console_url>]

Options:
  --dify-dir       Dify source directory. The script writes into <dify-dir>/docker.
  --app-id         Dify app id to guard. Repeat or pass comma-separated values.
  --node-id        Dify Agent node id to guard. Repeat or pass comma-separated values.
  --server-url     AgentGuard server API URL reachable from Dify containers.
  --api-key        Optional AgentGuard API key.
  --policy         Optional AgentGuard policy name or mounted rules path.
  --console-url    Optional AgentGuard frontend URL to print after setup.
  --bootstrap-dir  Optional bootstrap directory. Defaults to <dify-dir>/agentguard-dify-bootstrap.
  --output-file    Optional compose override path. Defaults to <dify-dir>/docker/docker-compose.agentguard.yml.
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

if [ -z "$DIFY_DIR" ] || [ -z "$APP_IDS" ] || [ -z "$NODE_IDS" ] || [ -z "$SERVER_URL" ]; then
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
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
PY

cat > "$OUTPUT_FILE" <<YAML
services:
  api:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_SERVER_URL: "$SERVER_URL"
      AGENTGUARD_API_KEY: "$API_KEY"
      AGENTGUARD_POLICY: "$POLICY"
      AGENTGUARD_DIFY_APP_IDS: "$APP_IDS"
      AGENTGUARD_DIFY_NODE_IDS: "$NODE_IDS"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - $AGENTGUARD_ROOT:/agentguard:ro
      - $BOOTSTRAP_DIR:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"

  worker:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_SERVER_URL: "$SERVER_URL"
      AGENTGUARD_API_KEY: "$API_KEY"
      AGENTGUARD_POLICY: "$POLICY"
      AGENTGUARD_DIFY_APP_IDS: "$APP_IDS"
      AGENTGUARD_DIFY_NODE_IDS: "$NODE_IDS"
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

if [ -n "$CONSOLE_URL" ]; then
    cat <<EOF
After running a workflow, open your AgentGuard console:
  $CONSOLE_URL
EOF
else
    cat <<'EOF'
After running a workflow, open the AgentGuard console provided by your AgentGuard server operator.
EOF
fi
