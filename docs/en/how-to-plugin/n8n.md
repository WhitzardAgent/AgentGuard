# n8n Unified Integration

This guide explains how to connect a locally deployed n8n instance to AgentGuard. n8n creates Agents, LLMs, AI Tools, and executable nodes inside the n8n runtime, so the integration is similar to Dify: no n8n source-code changes are required. The adapter is preloaded into the n8n Node.js process with `NODE_OPTIONS=--require ...`.

After integration, the AgentGuard frontend can show synced n8n workflows and tool catalogs before the workflow runs, so you can configure security rules ahead of time. Runtime events continue to be recorded into traces as calls happen.

## Quick Start: Connect n8n

Assumptions:

- AgentGuard source is at `/path/to/AgentGuard`
- n8n runs locally with Docker or Docker Compose
- You have an AgentGuard server URL and console URL

### 1. Prepare AgentGuard Server URLs

The n8n side only runs the AgentGuard client adapter. The AgentGuard server and frontend console are usually hosted by the AgentGuard service operator.

You need:

```text
AGENTGUARD_SERVER_URL=https://<your-agentguard-server>
AGENTGUARD_CONSOLE_URL=https://<your-agentguard-console>
```

For local testing, start AgentGuard on the host:

```bash
cd /path/to/AgentGuard
./scripts/start.sh --build -d
```

Then n8n containers usually reach the host server with:

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
AGENTGUARD_CONSOLE_URL=http://127.0.0.1:38008/agents.html
```

### 2. Generate Integration Files

By default, the integration covers all active / published workflows in the current n8n instance. No workflow allowlist is needed:

```bash
cd /path/to/AgentGuard
scripts/setup-n8n-agentguard.sh \
  --server-url <your_agentguard_server_url> \
  --api-key <your_agentguard_api_key> \
  --policy n8n_default \
  --console-url <your_agentguard_console_url>
```

Local test example:

```bash
scripts/setup-n8n-agentguard.sh \
  --server-url http://host.docker.internal:38080 \
  --console-url http://127.0.0.1:38008/agents.html
```

The script generates:

```text
/path/to/AgentGuard/agentguard-n8n-bootstrap/register.cjs
/path/to/AgentGuard/docker-compose.n8n-agentguard.yml
```

### 3. Start n8n With AgentGuard

If you use Docker Compose, run n8n with the generated override:

```bash
docker compose -f docker-compose.yml -f /path/to/AgentGuard/docker-compose.n8n-agentguard.yml up -d --force-recreate n8n
```

If you use `docker run`, keep your existing n8n volume and environment settings and add the AgentGuard options printed by the setup script. Minimal local example:

```bash
cd /path/to/AgentGuard
AGENTGUARD_ROOT="$(pwd)"

docker rm -f n8n || true
docker run -d --name n8n \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  -e N8N_RUNNERS_ENABLED=true \
  -e GENERIC_TIMEZONE=Asia/Shanghai \
  -e TZ=Asia/Shanghai \
  -e AGENTGUARD_ENABLED=true \
  -e AGENTGUARD_ENVIRONMENT=n8n \
  -e AGENTGUARD_ROOT=/agentguard \
  -e AGENTGUARD_SERVER_URL=http://host.docker.internal:38080 \
  -e AGENTGUARD_API_KEY=<your_agentguard_api_key> \
  -e AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true \
  -e AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S=5 \
  -e AGENTGUARD_N8N_DB_PATH=/home/node/.n8n/database.sqlite \
  -e NODE_OPTIONS="--require /agentguard-n8n-bootstrap/register.cjs" \
  -v "$AGENTGUARD_ROOT:/agentguard:ro" \
  -v "$AGENTGUARD_ROOT/agentguard-n8n-bootstrap:/agentguard-n8n-bootstrap:ro" \
  --add-host host.docker.internal:host-gateway \
  docker.n8n.io/n8nio/n8n:2.26.8
```

### 4. Configure Rules In AgentGuard

Open the AgentGuard console:

```text
<your_agentguard_console_url>
```

Refresh the Agents list. Synced n8n workflows appear with agent IDs like:

```text
n8n:<workflow_id>
```

Open an agent to inspect its tool catalog and configure rules before the workflow runs. Common tool sources include:

- n8n AI Tools connected to an Agent node, such as HTTP Request Tool.
- Ordinary executable nodes, such as HTTP Request and Code.

LLM, Agent, memory, parser, retriever, vector store, trigger, and logic/control nodes such as If / Switch / Merge are not registered as frontend tools. LLM / Agent runtime paths still emit `llm_input` / `llm_output` events.

### 5. Run n8n And Verify Traces

Run a workflow in n8n. A run with LLM and tool calls usually emits:

```text
llm_input
llm_output
tool_invoke
tool_result
```

AgentGuard session metadata includes:

```text
environment=n8n
workflow_id
workflow_name
execution_id
node_id
node_name
node_type
```

## Adapter Behavior

The n8n adapter is preloaded with:

```text
NODE_OPTIONS=--require /agentguard-n8n-bootstrap/register.cjs
```

The bootstrap installs:

```js
const { installN8nAdapter } = require("/agentguard/src/client/js/agentguard/adapters/agent/n8n");
installN8nAdapter();
```

The current adapter covers these runtime paths:

- `ChatOpenAIResponses.prototype.completionWithRetry` from `@langchain/openai`
- n8n Agent V3 engine request / action path
- n8n `WorkflowExecute.prototype.runNode`
- n8n Tools Agent connected-tools path

Tool deny / sanitize behavior:

- If `tool_invoke` is denied, the adapter does not call the real tool and returns an n8n-compatible blocked / pending result.
- If `tool_result` is denied or sanitized, the adapter returns a safe result without breaking n8n's native execution flow.

Tool event `arguments` are kept close to real tool business parameters. For n8n AI Tools, the adapter prefers the tool node's own `node.parameters`, such as HTTP Request Tool `url`, `method`, `authentication`, `headers`, `body`, and `options`. n8n runtime envelope fields such as `sessionId`, `toolCallId`, `action`, and `chatInput` are stored in event metadata instead of tool arguments.

OpenAI Responses API built-in tools such as `web_search`, `file_search`, and `code_interpreter` run inside the model provider. The adapter does not emit `tool_invoke` / `tool_result` for them. If the request enables those provider-side tools, `llm_input.metadata` records:

```text
model_builtin_tools
model_builtin_tools_hooked=false
model_builtin_tools_reason=provider_side_execution
```

## Pre-Run Tool Catalog Sync

The adapter periodically scans n8n's SQLite database for active / published workflows and syncs the tool catalog to the AgentGuard server. The default interval is 5 seconds:

```text
AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true
AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S=5
AGENTGUARD_N8N_DB_PATH=/home/node/.n8n/database.sqlite
```

During agent registration, the adapter reads each workflow owner email from n8n's database and binds with `provider=n8n + account_email=<workflow_owner_email>`. A single n8n container can therefore host workflows owned by multiple n8n users. Do not hard-code one account email as a container-wide environment variable. Each AgentGuard user only needs to bind their own n8n email in the User Centre. After sync, the AgentGuard frontend can show the `n8n:<workflow_id>` agent and its tool catalog even before the workflow runs. When a workflow is modified and saved / published, the next scan syncs the updated catalog.

## Supported Scope

Validated support currently includes:

- n8n 2.26.8 local Docker deployment.
- Chat Trigger -> AI Agent -> OpenAI Chat Model LLM input/output events.
- Tool calls and tool results for n8n AI Tools connected to an Agent.
- Ordinary HTTP Request / Code nodes recorded as tools.
- Pre-run tool catalog sync for active / published workflows.
- Adapter preload in the main process and task runner when `N8N_RUNNERS_ENABLED=true`.

Not covered yet:

- Provider-side built-in tools such as OpenAI `web_search`, `file_search`, and `code_interpreter`.
- If / Switch / Merge / Trigger / Agent / LLM / memory / parser / retriever / vector store nodes as tool events.
- Workflow catalog scans for non-SQLite n8n database deployments.

## Manual Integration Files

If you do not use the script, create `/path/to/AgentGuard/agentguard-n8n-bootstrap/register.cjs` manually:

```js
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
```

Then create a Docker Compose override:

```yaml
services:
  n8n:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_ENVIRONMENT: "n8n"
      AGENTGUARD_ROOT: "/agentguard"
      AGENTGUARD_SERVER_URL: "http://host.docker.internal:38080"
      AGENTGUARD_API_KEY: ""
      AGENTGUARD_POLICY: ""
      AGENTGUARD_N8N_NODE_IDS: ""
      AGENTGUARD_N8N_SKIP_NODE_TYPES: ""
      AGENTGUARD_N8N_CATALOG_SYNC_ENABLED: "true"
      AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S: "5"
      AGENTGUARD_N8N_DB_PATH: "/home/node/.n8n/database.sqlite"
      NODE_OPTIONS: "--require /agentguard-n8n-bootstrap/register.cjs"
    volumes:
      - /path/to/AgentGuard:/agentguard:ro
      - /path/to/AgentGuard/agentguard-n8n-bootstrap:/agentguard-n8n-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

On Linux Docker, `host.docker.internal` requires the `host-gateway` mapping in `extra_hosts`.

## Troubleshooting

### n8n Agents Do Not Appear In AgentGuard

Check:

- You are logged in to the correct AgentGuard console.
- The n8n container can reach `AGENTGUARD_SERVER_URL`.
- The n8n container has the same `AGENTGUARD_API_KEY` as the AgentGuard server. A missing or wrong key makes `/v1/server/agents/register` fail.
- `NODE_OPTIONS` includes `/agentguard-n8n-bootstrap/register.cjs`.
- The n8n container mounts the AgentGuard source and bootstrap directory.
- `AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true` is set.
- `AGENTGUARD_N8N_DB_PATH` points to the SQLite database inside the n8n container.
- The current workflow owner email is bound in the AgentGuard User Centre. The adapter binds by workflow owner email; do not hard-code one email for the whole container.
- The workflow is active or published.

By default, no workflow allowlist is needed. If the container still has an old `AGENTGUARD_N8N_WORKFLOW_IDS` value, the adapter only connects those workflows. For multi-agent use, remove that variable and recreate the n8n container.

### Confirm Adapter Installation

Check n8n logs:

```bash
docker logs n8n 2>&1 | rg "agentguard:n8n|catalog sync"
```

Logs like these mean the hook was installed and catalog sync started:

```text
[agentguard:n8n] adapter installed
[agentguard:n8n] bootstrap status { installed: true, catalog_sync: ... }
[agentguard:n8n] catalog sync complete ...
```

### Environment Variable Changes Do Not Apply

Docker container environment variables are fixed when the container is created. After editing the compose file, recreate the n8n container:

```bash
docker compose -f docker-compose.yml -f /path/to/AgentGuard/docker-compose.n8n-agentguard.yml up -d --force-recreate n8n
```

For `docker run` deployments, remove the old container and run it again.
