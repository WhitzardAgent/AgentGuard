# Dify Agent And Workflow Agent Integration

This guide explains how to connect locally deployed Dify agents to AgentGuard. Dify has two common runtime paths, and they use different adapters:

- **Agent Chat app**: the URL usually looks like `/app/<app_id>/configuration`, and the Dify app mode is `agent-chat`. This path runs through `AgentChatAppRunner` and uses the `dify_agent_chat` adapter.
- **Workflow Agent node**: the URL usually looks like `/app/<app_id>/workflow`, and the workflow graph contains an Agent node. This path runs through the workflow runtime and uses the `dify` workflow adapter.

Neither path requires Dify source-code changes. The recommended deployment method installs AgentGuard adapters through `sitecustomize.py` when Dify `api` / `worker` Python processes start, and mounts the AgentGuard client with a Docker Compose override.

## Quick Start: Dify Agent Chat App

Use this path when you already built a Dify Agent Chat app.

Assumptions:

- AgentGuard source is at `/path/to/AgentGuard`
- Dify source is at `/path/to/dify`
- Dify runs locally with Docker Compose
- Your Dify app URL looks like `/app/<app_id>/configuration`
- You have an AgentGuard server URL and console URL

### 1. Prepare AgentGuard Server URLs

The Dify side only runs the AgentGuard client adapter. The AgentGuard server and frontend console are usually hosted by the AgentGuard service operator.

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

Then Dify containers usually reach the host server with:

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
AGENTGUARD_CONSOLE_URL=http://127.0.0.1:38008/agents.html
```

### 2. Get The Dify app_id

Open the Agent Chat app configuration page. The URL usually looks like:

```text
http://127.0.0.1/app/6680db75-b1ed-4735-b4b4-a76efe1b7b42/configuration
```

The value below is the `app_id`:

```text
6680db75-b1ed-4735-b4b4-a76efe1b7b42
```

AgentGuard maps this app to this console `agent_id`:

```text
dify-agent-chat:<app_id>
```

Different users of the same Dify app share the same `agent_id`, but each message includes Dify `user_id`, `conversation_id`, and `message_id`, so the server and policies can still distinguish users and messages.

### 3. Generate Integration Files

From the AgentGuard directory:

```bash
cd /path/to/AgentGuard
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --agent-chat \
  --app-id <your_app_id> \
  --server-url <your_agentguard_server_url> \
  --api-key <your_agentguard_api_key> \
  --console-url <your_agentguard_console_url>
```

Local test example:

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --agent-chat \
  --app-id 6680db75-b1ed-4735-b4b4-a76efe1b7b42 \
  --server-url http://host.docker.internal:38080 \
  --console-url http://127.0.0.1:38008/agents.html
```

The script generates:

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

The generated `sitecustomize.py` installs both the Agent Chat adapter and the workflow adapter. Agent Chat is controlled by `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true`.

### 4. Start Dify With AgentGuard

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

If you only validate an Agent Chat app, the key process is `api`; recreating `worker` is useful when you also test workflows.

### 5. Verify

Open Dify and run your Agent Chat app. Use a prompt that triggers a tool call.

Then open the AgentGuard console:

```text
<your_agentguard_console_url>
```

Refresh the Agent list and look for:

```text
dify-agent-chat:<app_id>
```

A tool-using Agent Chat run usually emits:

```text
llm_input
llm_output
tool_invoke
tool_result
```

The tool catalog reports only tools enabled for the current runtime turn. The adapter uses `BaseAgentRunner._init_prompt_tools()` return value, `tool_instances`, as the registration source, so tools configured with `enabled=false` in Dify are not reported.

## Agent Chat Adapter Behavior

The Agent Chat adapter entry point is:

```python
from agentguard.adapters.agent.dify_agent_chat import install_dify_agent_chat_adapter

install_dify_agent_chat_adapter()
```

It patches these Dify call sites:

- `core.app.apps.agent_chat.app_runner.AgentChatAppRunner.run`
- `core.agent.base_agent_runner.BaseAgentRunner._init_prompt_tools`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

At run start, the adapter creates an AgentGuard session:

```text
agent_id   = dify-agent-chat:<app_id>
session_id = <message_id>, falling back to task_id
user_id    = Dify application_generate_entity.user_id
```

Event metadata includes:

```text
tenant_id
app_id
conversation_id
message_id
user_id
task_id
invoke_from
agent_strategy
```

Server-side policies and filters can use `principal.agent_id`, `principal.user_id`, and `principal.session_id`.

Tool deny / sanitize behavior:

- If `tool_invoke` is denied, the adapter does not call the real tool and returns a Dify-compatible tool error observation.
- If `tool_result` is denied or sanitized, the adapter returns a safe observation without breaking Dify's native message-record flow.

## Quick Start: Dify Workflow Agent Node

Use this path when your Dify agent is an Agent node inside a workflow graph.

### 1. Confirm The Legacy Agent Path

The validated path is the legacy Workflow Agent node with `ENABLE_AGENT_V2=false`. Check Dify `.env`:

```bash
cd /path/to/dify/docker
rg 'ENABLE_AGENT_V2|AGENT_BACKEND_BASE_URL' .env
```

Recommended settings:

```text
ENABLE_AGENT_V2=false
AGENT_BACKEND_BASE_URL=
```

### 2. Get app_id And Agent node_id

The `app_id` is in the workflow page URL:

```text
http://127.0.0.1/app/ce0aa322-1f3f-4ab9-8329-3af8588c7480/workflow
```

Dify may not show the node ID directly. Querying the Dify database is the recommended method:

```bash
cd /path/to/dify/docker
docker compose exec -T db_postgres psql -U postgres -d dify -c "
select
  node->>'id' as node_id,
  node->'data'->>'title' as title,
  node->'data'->>'type' as type
from workflows w
cross join lateral jsonb_array_elements(w.graph::jsonb->'nodes') as node
where w.app_id='<your_app_id>'
  and w.version='draft'
  and node->'data'->>'type'='agent';
"
```

The output `node_id` is the Agent node ID.

### 3. Generate Integration Files

```bash
cd /path/to/AgentGuard
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --app-id <your_app_id> \
  --node-id <your_agent_node_id> \
  --server-url <your_agentguard_server_url> \
  --api-key <your_agentguard_api_key> \
  --policy dify_default \
  --console-url <your_agentguard_console_url>
```

For multiple workflows or Agent nodes, repeat options or pass comma-separated values.

### 4. Start And Verify

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

Run the workflow and refresh the AgentGuard console. A tool-using Workflow Agent node usually emits:

```text
llm_input
llm_output
tool_invoke
tool_result
llm_input
llm_output
```

## Workflow Adapter Behavior

The workflow adapter entry point is:

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

It patches these legacy Workflow Agent in-process call sites:

- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

It also patches plugin daemon backwards invocation:

- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

The backwards invocation hooks are important because Dify legacy Agents often call `/invoke/llm` and `/invoke/tool` through the plugin daemon. In that case, the real LLM/tool execution happens in the API process rather than the worker process that entered the Agent node.

## Supported Scope

Validated support currently includes:

- Dify 1.15.x local source deployment.
- Legacy `agent-chat` apps.
- Legacy Workflow Agent nodes with `ENABLE_AGENT_V2=false`.
- LLM calls, LLM outputs, tool calls, and tool results inside Agent Chat / Workflow Agent runtime.
- Reporting the runtime-available tool catalog at run start or first tool initialization.

Not covered yet:

- Normal Workflow LLM nodes.
- Standalone Workflow Tool nodes.
- Graphs where an LLM node routes to separate Tool nodes.
- Fully validated Dify Agent v2 backend / `dify-agent` service integration.

## Manual Integration Files

If you do not use the script, create `/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py` manually:

```python
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
```

Then create `/path/to/dify/docker/docker-compose.agentguard.yml`:

```yaml
services:
  api:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_DIFY_AGENT_CHAT_ENABLED: "true"
      AGENTGUARD_SERVER_URL: "http://host.docker.internal:38080"
      AGENTGUARD_API_KEY: ""
      AGENTGUARD_POLICY: ""
      AGENTGUARD_DIFY_APP_IDS: "<optional_app_id_filter>"
      AGENTGUARD_DIFY_NODE_IDS: "<optional_workflow_node_id_filter>"
      AGENTGUARD_ENVIRONMENT: "dify"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - /path/to/AgentGuard:/agentguard:ro
      - /path/to/dify/agentguard-dify-bootstrap:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"

  worker:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_DIFY_AGENT_CHAT_ENABLED: "true"
      AGENTGUARD_SERVER_URL: "http://host.docker.internal:38080"
      AGENTGUARD_API_KEY: ""
      AGENTGUARD_POLICY: ""
      AGENTGUARD_DIFY_APP_IDS: "<optional_app_id_filter>"
      AGENTGUARD_DIFY_NODE_IDS: "<optional_workflow_node_id_filter>"
      AGENTGUARD_ENVIRONMENT: "dify"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - /path/to/AgentGuard:/agentguard:ro
      - /path/to/dify/agentguard-dify-bootstrap:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

On Linux Docker, `host.docker.internal` requires the `host-gateway` mapping in `extra_hosts`.

## Troubleshooting

### Dify UI Is Stuck Or API Returns 502

If you recreated `api`, nginx may still point to the old upstream IP. Run:

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

### Dify Agent Does Not Appear In AgentGuard

Check:

- You are logged in to the correct AgentGuard console.
- Dify containers can reach `AGENTGUARD_SERVER_URL`.
- `PYTHONPATH` includes the bootstrap and AgentGuard client paths.
- Both `api` and `worker` mount AgentGuard and the bootstrap directory.
- Agent Chat apps have `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true`.
- If `AGENTGUARD_DIFY_APP_IDS` is set, the current app id is in the list.
- Workflow Agent nodes use the correct `AGENTGUARD_DIFY_NODE_IDS`.
- The agent actually initialized or invoked tools; the tool catalog is usually reported at run start or first tool initialization.

### Confirm Adapter Installation

Check Dify `api` logs:

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs api \
  | rg "AgentGuard Dify Agent Chat adapter status|AgentGuard Dify workflow adapter status"
```

`patched: True` means the hook was installed.

### What Happens When A Tool Is Denied

If `tool_invoke` is denied or pending, the adapter does not call the real tool. It returns a blocked/pending observation that the Agent can read and continue reasoning over.
