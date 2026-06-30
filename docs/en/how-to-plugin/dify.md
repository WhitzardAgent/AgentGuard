# Dify Workflow Agent Node

This guide explains how to connect a locally deployed Dify Workflow Agent node to AgentGuard. It assumes you have cloned the Dify source code, run Dify locally with Docker Compose, and built your workflow in the Dify UI.

## Supported Scope

The current Dify adapter is validated for:

- Dify 1.15.x local source deployment.
- The legacy Workflow Agent path with `ENABLE_AGENT_V2=false`.
- Workflows that contain an Agent node, for example `Start -> Agent -> End`.
- Tools configured inside the Agent node.
- LLM calls, LLM responses, tool calls, and tool results that happen inside the Agent node.
- Tool catalog reporting for tools actually invoked by the Agent node, so the AgentGuard frontend can discover the Dify agent.

The adapter emits:

- `llm_input`
- `llm_output`
- `tool_invoke`
- `tool_result`

If an LLM turn only requests a tool and does not produce natural-language text, `llm_output` remains empty:

```json
{
  "output": null,
  "thought": null,
  "final_output": null
}
```

The tool name and arguments are represented by the following `tool_invoke` event.

## Not Supported Yet

The current adapter does not cover:

- Normal Workflow LLM nodes.
- Standalone Workflow Tool nodes.
- Graphs where an LLM node routes to separate Tool nodes.
- All Dify Agent App or Chat App paths.
- Fully validated Dify Agent v2 backend / `dify-agent` service integration.

Some Agent v2 hooks exist in the code, but the recommended and validated path is the legacy Workflow Agent node path described here.

## How It Works

Dify creates Workflow Agent nodes, LLM models, and tools internally. There is no user-owned agent object to pass into `guard.attach_xxx()`.

For Dify, install the runtime adapter once when the Dify `api` and `worker` Python processes start:

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

The recommended deployment path does not modify Dify source code. It uses `sitecustomize.py` so Python automatically installs the adapter during process startup.

The adapter patches these legacy Workflow Agent call sites:

- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

It also patches the plugin daemon backwards invocation path:

- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

The backwards invocation hooks are important because Dify legacy Agent strategies often call LLMs and tools through plugin daemon callbacks. In that case the real LLM/tool work happens in the API process, not inside the worker process that entered the Agent node.

## Security Behavior

Before an LLM call, the adapter emits `llm_input` with the prompt messages Dify already constructed.

After an LLM call, it emits `llm_output` with only `output`, `thought`, and `final_output`. Tool-call-only turns keep all three fields as `null`.

Before a tool call, it emits `tool_invoke` with the tool name and arguments. If AgentGuard returns deny or pending, the adapter does not call the real tool and returns a readable blocked/pending observation to the Agent.

After a tool call, it emits `tool_result` with the tool result text. If the result phase returns deny or sanitize, the adapter returns the handled observation to the Agent.

## Prepare Dify

Clone and run Dify with Docker Compose. This guide assumes Dify lives at:

```bash
/path/to/dify
```

Make sure Dify uses the legacy Agent path:

```text
ENABLE_AGENT_V2=false
AGENT_BACKEND_BASE_URL=
```

Create or open your workflow in the Dify UI and confirm the graph contains an Agent node with tools configured inside that node.

## Get app_id And node_id

Record the IDs used to scope AgentGuard to one workflow Agent node.

The `app_id` is usually in the browser URL:

```text
http://localhost/app/<app_id>/workflow
```

The Agent node `node_id` can be found from the workflow DSL, debug events, workflow run events, or the database. During a workflow run, Dify SSE events contain data like:

```json
{
  "event": "node_started",
  "data": {
    "node_id": "1782713638856",
    "node_type": "agent"
  }
}
```

Here `1782713638856` is the Agent node ID.

## Recommended: Generate Deployment Files

AgentGuard provides a helper script that generates the required `sitecustomize.py` and Compose override:

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

For multiple workflows or Agent nodes, repeat the options or pass comma-separated values:

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --app-id app_id_1 --app-id app_id_2 \
  --node-id node_id_1 --node-id node_id_2 \
  --server-url <your_agentguard_server_url>
```

The script generates:

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

The generated `sitecustomize.py` only installs the adapter:

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

The generated Compose override mounts AgentGuard and the bootstrap directory into Dify `api` and `worker`, and configures:

- `AGENTGUARD_ENABLED`
- `AGENTGUARD_SERVER_URL`
- `AGENTGUARD_API_KEY`
- `AGENTGUARD_POLICY`
- `AGENTGUARD_DIFY_APP_IDS`
- `AGENTGUARD_DIFY_NODE_IDS`
- `PYTHONPATH`

## Prepare AgentGuard Server Connection

Users only run the AgentGuard client adapter on the Dify side. The AgentGuard server and frontend console are normally hosted by the AgentGuard service operator.

Make sure the server API URL is reachable from Dify containers:

```text
AGENTGUARD_SERVER_URL=<your_agentguard_server_url>
```

For local development, if the AgentGuard server runs on the host at port `38080`, use:

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
```

On Linux Docker, the generated override includes:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

## Start Dify With AgentGuard

Run:

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

Restarting `nginx` is recommended after recreating `api`, because the container IP can change and nginx may still point to the old upstream.

## Verify In The Frontend

Run your Dify workflow, then open the AgentGuard console provided by your server operator:

```text
<your_agentguard_console_url>
```

Log in and refresh the Agent list. If the Agent node invoked a tool, you should see the Dify agent and its tool. Open the Runtime page to inspect the event sequence.

A tool-using Agent node usually produces:

```text
llm_input
llm_output
tool_invoke
tool_result
llm_input
llm_output
```

If the first LLM turn only decides to call a tool, `llm_output` is:

```json
{
  "output": null,
  "thought": null,
  "final_output": null
}
```

The following `tool_invoke` contains the tool name and arguments.

## Connecting A New Workflow

For a new workflow with an Agent node:

1. Keep `ENABLE_AGENT_V2=false`.
2. Get the new `app_id`.
3. Get the Agent node `node_id`.
4. Run `scripts/setup-dify-agentguard.sh` again, or manually update `AGENTGUARD_DIFY_APP_IDS` and `AGENTGUARD_DIFY_NODE_IDS` in `docker-compose.agentguard.yml`.
5. Recreate `api` and `worker`, then restart `nginx`.
6. Run the workflow and inspect the AgentGuard console provided by your server operator.

No Dify source code changes are required.

## Troubleshooting

If the UI is stuck or API requests return 502, restart nginx:

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

If the Dify agent does not appear in the AgentGuard frontend, check:

- You are logged in to the correct AgentGuard console.
- Dify containers can reach `AGENTGUARD_SERVER_URL`.
- The workflow really reached the target Agent node.
- The Agent node actually invoked a tool. Tool catalog entries are reported on first tool invocation.
- `AGENTGUARD_DIFY_APP_IDS` and `AGENTGUARD_DIFY_NODE_IDS` match the current workflow.
- `PYTHONPATH` includes the bootstrap and AgentGuard client paths.
- Both `api` and `worker` mount AgentGuard and the bootstrap directory.

If the workflow succeeds but the server receives no events, check Dify can reach AgentGuard:

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml exec api \
  curl -sS <your_agentguard_server_url>/health
```

If your AgentGuard server requires authentication or uses a different health path, use the test command provided by the server operator.
