# Dify Workflow Agent Node

This guide explains how to connect a locally deployed Dify Workflow Agent node to AgentGuard. It assumes you have cloned the Dify source code, run Dify locally with Docker Compose, and built your workflow in the Dify UI.

## Supported Scope

The current Dify adapter is validated for:

- Dify 1.15.x local source deployment.
- The legacy Workflow Agent path with `ENABLE_AGENT_V2=false`.
- Workflows that contain an Agent node, for example `Start -> Agent -> End`.
- Tools configured inside the Agent node.
- LLM calls, LLM responses, tool calls, and tool results that happen inside the Agent node.

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

The adapter patches these legacy Workflow Agent call sites:

- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

It also patches the plugin daemon backwards invocation path:

- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

The backwards invocation hooks are important because Dify legacy Agent strategies often call LLMs and tools through plugin daemon callbacks. In that case the real LLM/tool work happens in the API process, not inside the worker process that entered the Agent node.

## Create Bootstrap

Create a bootstrap directory:

```bash
mkdir -p /path/to/agentguard-dify-bootstrap
```

Create `/path/to/agentguard-dify-bootstrap/sitecustomize.py`:

```python
from agentguard.adapters.agent.dify import install_dify_adapter

print("[AgentGuard] Dify adapter:", install_dify_adapter(), flush=True)
```

Python imports `sitecustomize` automatically if it is on `PYTHONPATH`.

## Compose Override

Create `/path/to/dify/docker/docker-compose.agentguard.yml`:

```yaml
services:
  api:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_SERVER_URL: "http://host.docker.internal:38080"
      AGENTGUARD_API_KEY: ""
      AGENTGUARD_POLICY: "dify_default"
      AGENTGUARD_DIFY_APP_IDS: "<your_app_id>"
      AGENTGUARD_DIFY_NODE_IDS: "<your_agent_node_id>"
      AGENTGUARD_DIFY_PRINT_EVENTS: "true"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - /path/to/AgentGuard:/agentguard:ro
      - /path/to/agentguard-dify-bootstrap:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"

  worker:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_SERVER_URL: "http://host.docker.internal:38080"
      AGENTGUARD_API_KEY: ""
      AGENTGUARD_POLICY: "dify_default"
      AGENTGUARD_DIFY_APP_IDS: "<your_app_id>"
      AGENTGUARD_DIFY_NODE_IDS: "<your_agent_node_id>"
      AGENTGUARD_DIFY_PRINT_EVENTS: "true"
      PYTHONPATH: "/agentguard-dify-bootstrap:/agentguard/src/client/python:/agentguard/src:/app/api"
    volumes:
      - /path/to/AgentGuard:/agentguard:ro
      - /path/to/agentguard-dify-bootstrap:/agentguard-dify-bootstrap:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

`AGENTGUARD_DIFY_APP_IDS` and `AGENTGUARD_DIFY_NODE_IDS` can contain comma-separated values. If they are omitted, the adapter attempts to guard all legacy Workflow Agent nodes.

## Start Dify With AgentGuard

Make sure Dify uses the legacy Agent path:

```text
ENABLE_AGENT_V2=false
AGENT_BACKEND_BASE_URL=
```

Start or recreate the relevant services:

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

Restarting `nginx` is recommended after recreating `api`, because the container IP can change and nginx may still point to the old upstream.

Check the logs:

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs -f api worker
```

The adapter status should show:

```text
plugin_backwards_llm: True
plugin_backwards_tool: True
```

## Verify Events

Run your Dify workflow and watch:

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs -f api worker \
  | rg 'AgentGuard Event|AgentGuard LLMOutput Parsed'
```

A tool-using Agent node usually produces:

```text
llm_input
llm_output
tool_invoke
tool_result
llm_input
llm_output
```

## Connecting A New Workflow

For a new workflow with an Agent node:

1. Keep `ENABLE_AGENT_V2=false`.
2. Get the new `app_id` from `/app/<app_id>/workflow`.
3. Get the Agent node `node_id` from the workflow run events or workflow DSL.
4. Update:

```text
AGENTGUARD_DIFY_APP_IDS=<new_app_id>
AGENTGUARD_DIFY_NODE_IDS=<new_agent_node_id>
```

5. Recreate `api` and `worker`, then restart `nginx`.
6. Run the workflow and check AgentGuard events.

No Dify source code changes are required.

## Troubleshooting

If the UI is stuck or API requests return 502, restart nginx:

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

If no events appear, check:

- `AGENTGUARD_ENABLED=true`
- `AGENTGUARD_DIFY_PRINT_EVENTS=true`
- `PYTHONPATH` includes the bootstrap and AgentGuard client paths
- Both `api` and `worker` mount AgentGuard and the bootstrap directory
- The startup log shows `plugin_backwards_llm` and `plugin_backwards_tool` as true
- The current `app_id` and Agent node `node_id` match the filters

