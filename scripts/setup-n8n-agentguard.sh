#!/usr/bin/env bash
# Generate deployment-side files for connecting a local n8n container to
# AgentGuard without modifying n8n source code.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTGUARD_ROOT="$(dirname "$SCRIPT_DIR")"

SERVER_URL="http://host.docker.internal:38080"
CONSOLE_URL=""
API_KEY=""
POLICY=""
PLUGIN_CONFIG=""
NODE_IDS=""
SKIP_NODE_TYPES=""
BOOTSTRAP_DIR=""
OUTPUT_FILE=""
SERVICE_NAME="n8n"
CONTAINER_NAME="n8n"
CATALOG_SYNC_INTERVAL_S="5"
CATALOG_INITIAL_DELAY_S="2"
CATALOG_DB_PATH="/home/node/.n8n/database.sqlite"

usage() {
    cat <<'EOF'
Usage:
  scripts/setup-n8n-agentguard.sh \
    [--server-url <agentguard_server_url>] \
    [--api-key <agentguard_api_key>] \
    [--policy <policy_name_or_path>] \
    [--plugin-config <json_or_path>] \
    [--node-id <n8n_node_id_or_name>] \
    [--console-url <agentguard_console_url>]

Options:
  --server-url      AgentGuard server API URL reachable from the n8n container.
                    Defaults to http://host.docker.internal:38080.
  --api-key         AgentGuard API key. Required when the server has AGENTGUARD_API_KEY set.
  --policy          Optional AgentGuard policy name or mounted rules path.
  --plugin-config   Optional AgentGuard plugin config passed through as
                    AGENTGUARD_PLUGIN_CONFIG. Accepts a JSON object string or
                    a mounted file path.
  --node-id         Optional n8n node id/name filter. Repeat or pass comma-separated values.
                    Omit to guard all eligible nodes.
  --skip-node-type  Optional extra n8n node type to skip as a tool.
                    Repeat or pass comma-separated values.
  --catalog-interval Seconds between published workflow catalog sync scans.
                    Defaults to 5. Set to 0 for one initial scan only.
  --catalog-db-path  Path to n8n's SQLite database inside the container.
                    Defaults to /home/node/.n8n/database.sqlite.
  --bootstrap-dir   Optional bootstrap directory.
                    Defaults to <AgentGuard>/agentguard-n8n-bootstrap.
  --output-file     Optional compose override path.
                    Defaults to <AgentGuard>/docker-compose.n8n-agentguard.yml.
  --service-name    n8n service name in Docker Compose. Defaults to n8n.
  --container-name  Existing/local Docker container name to show in docker run examples.
                    Defaults to n8n.
  --console-url     Optional AgentGuard frontend URL to print after setup.
  -h, --help        Show this help.

The generated compose override expects the base compose file to define a service
named by --service-name. For docker run deployments, use the printed run example
as a template and preserve your existing n8n volume/environment settings.
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
        --plugin-config)
            PLUGIN_CONFIG="${2:-}"
            shift 2
            ;;
        --node-id|--node-ids)
            NODE_IDS="$(append_csv "$NODE_IDS" "${2:-}")"
            shift 2
            ;;
        --skip-node-type|--skip-node-types)
            SKIP_NODE_TYPES="$(append_csv "$SKIP_NODE_TYPES" "${2:-}")"
            shift 2
            ;;
        --catalog-interval)
            CATALOG_SYNC_INTERVAL_S="${2:-}"
            shift 2
            ;;
        --catalog-db-path)
            CATALOG_DB_PATH="${2:-}"
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
        --service-name)
            SERVICE_NAME="${2:-}"
            shift 2
            ;;
        --container-name)
            CONTAINER_NAME="${2:-}"
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

BOOTSTRAP_DIR="${BOOTSTRAP_DIR:-$AGENTGUARD_ROOT/agentguard-n8n-bootstrap}"
OUTPUT_FILE="${OUTPUT_FILE:-$AGENTGUARD_ROOT/docker-compose.n8n-agentguard.yml}"

mkdir -p "$BOOTSTRAP_DIR"

cat > "$BOOTSTRAP_DIR/register.cjs" <<'JS'
"use strict";

const path = require("path");

const agentGuardRoot = process.env.AGENTGUARD_ROOT || "/agentguard";
const adapterPath = path.join(
  agentGuardRoot,
  "src/client/js/agentguard/adapters/agent/n8n"
);

try {
  const { installN8nAdapter } = require(adapterPath);
  const status = installN8nAdapter();
  console.warn("[agentguard:n8n] bootstrap status", status);
} catch (error) {
  console.warn("[agentguard:n8n] bootstrap failed", error && error.stack ? error.stack : String(error));
}
JS

cat > "$OUTPUT_FILE" <<YAML
services:
  $SERVICE_NAME:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_ENVIRONMENT: "n8n"
      AGENTGUARD_ROOT: "/agentguard"
      AGENTGUARD_SERVER_URL: "$SERVER_URL"
      AGENTGUARD_API_KEY: "$API_KEY"
      AGENTGUARD_POLICY: "$POLICY"
      AGENTGUARD_PLUGIN_CONFIG: '$PLUGIN_CONFIG'
      AGENTGUARD_N8N_NODE_IDS: "$NODE_IDS"
      AGENTGUARD_N8N_SKIP_NODE_TYPES: "$SKIP_NODE_TYPES"
      AGENTGUARD_N8N_CATALOG_SYNC_ENABLED: "true"
      AGENTGUARD_N8N_CATALOG_INITIAL_DELAY_S: "$CATALOG_INITIAL_DELAY_S"
      AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S: "$CATALOG_SYNC_INTERVAL_S"
      AGENTGUARD_N8N_DB_PATH: "$CATALOG_DB_PATH"
      NODE_OPTIONS: "--require /agentguard-n8n-bootstrap/register.cjs"
    volumes:
      - $AGENTGUARD_ROOT:/agentguard:ro
      - $BOOTSTRAP_DIR:/agentguard-n8n-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
YAML

cat <<EOF
Generated:
  $BOOTSTRAP_DIR/register.cjs
  $OUTPUT_FILE

Start n8n with AgentGuard using Docker Compose:
  docker compose -f docker-compose.yml -f $(basename "$OUTPUT_FILE") up -d --force-recreate $SERVICE_NAME

For a docker run deployment, preserve your existing n8n volume/env settings and add:
  -e AGENTGUARD_ENABLED=true \\
  -e AGENTGUARD_ENVIRONMENT=n8n \\
  -e AGENTGUARD_ROOT=/agentguard \\
  -e AGENTGUARD_SERVER_URL=$SERVER_URL \\
  -e AGENTGUARD_API_KEY=$API_KEY \\
  -e AGENTGUARD_POLICY=$POLICY \\
  -e AGENTGUARD_PLUGIN_CONFIG='$PLUGIN_CONFIG' \\
  -e AGENTGUARD_N8N_NODE_IDS=$NODE_IDS \\
  -e AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true \\
  -e AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S=$CATALOG_SYNC_INTERVAL_S \\
  -e AGENTGUARD_N8N_DB_PATH=$CATALOG_DB_PATH \\
  -e NODE_OPTIONS="--require /agentguard-n8n-bootstrap/register.cjs" \\
  -v $AGENTGUARD_ROOT:/agentguard:ro \\
  -v $BOOTSTRAP_DIR:/agentguard-n8n-bootstrap:ro \\
  --add-host host.docker.internal:host-gateway

Minimal local example:
  docker rm -f $CONTAINER_NAME || true
  docker run -d --name $CONTAINER_NAME \\
    -p 5678:5678 \\
    -v n8n_data:/home/node/.n8n \\
    -e N8N_RUNNERS_ENABLED=true \\
    -e GENERIC_TIMEZONE=Asia/Shanghai \\
    -e TZ=Asia/Shanghai \\
    -e AGENTGUARD_ENABLED=true \\
    -e AGENTGUARD_ENVIRONMENT=n8n \\
    -e AGENTGUARD_ROOT=/agentguard \\
    -e AGENTGUARD_SERVER_URL=$SERVER_URL \\
    -e AGENTGUARD_API_KEY=$API_KEY \\
    -e AGENTGUARD_PLUGIN_CONFIG='$PLUGIN_CONFIG' \\
    -e AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true \\
    -e AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S=$CATALOG_SYNC_INTERVAL_S \\
    -e AGENTGUARD_N8N_DB_PATH=$CATALOG_DB_PATH \\
    -e NODE_OPTIONS="--require /agentguard-n8n-bootstrap/register.cjs" \\
    -v $AGENTGUARD_ROOT:/agentguard:ro \\
    -v $BOOTSTRAP_DIR:/agentguard-n8n-bootstrap:ro \\
    --add-host host.docker.internal:host-gateway \\
    docker.n8n.io/n8nio/n8n:2.26.8

EOF

if [ -n "$CONSOLE_URL" ]; then
    cat <<EOF
Open your AgentGuard console to inspect n8n traces:
  $CONSOLE_URL
EOF
else
    cat <<'EOF'
Open the AgentGuard console provided by your AgentGuard server operator to inspect n8n traces.
EOF
fi
