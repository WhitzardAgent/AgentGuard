# Dify Unified Integration

This guide explains how to connect a locally deployed Dify instance to AgentGuard. The current adapters cover the two main Dify agent forms:

- **Legacy Agent Chat apps**: the URL usually looks like `/app/<app_id>/configuration`, the Dify app mode is `agent-chat`, and integration is handled by the `dify_agent_chat` adapter.
- **Workflow / Chatflow apps**: the URL usually looks like `/app/<app_id>/workflow`, integration is handled by the `dify` workflow adapter, and LLM, Agent, Tool, and executable nodes in the graph are covered automatically.

Both forms use the same deployment path and do not require Dify source-code changes. The recommended setup installs AgentGuard adapters through `sitecustomize.py` when Dify `api` / `worker` Python processes start, and mounts the AgentGuard client with a Docker Compose override.

After integration, the AgentGuard frontend can show synced Dify agents / tool catalogs before the app is run, so you can configure security rules ahead of time. Runtime events continue to be recorded into traces as calls happen.

## Quick Start: Connect Dify

Assumptions:

- AgentGuard source is at `/path/to/AgentGuard`
- Dify source is at `/path/to/dify`
- Dify runs locally with Docker Compose
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

### 2. Generate Integration Files

To connect all legacy Agent Chat and Workflow/Chatflow apps in the current Dify instance, omit `--app-id`:

```bash
cd /path/to/AgentGuard
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --server-url <your_agentguard_server_url> \
  --api-key <your_agentguard_api_key> \
  --policy dify_default \
  --console-url <your_agentguard_console_url>
```

Local test example:

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --server-url http://host.docker.internal:38080 \
  --console-url http://127.0.0.1:38008/agents.html
```

To connect only selected apps, get the `app_id` from the Dify URL:

```text
http://127.0.0.1/app/bdec9bf4-a065-4066-8472-fe6a594a1bdd/workflow
```

Here `bdec9bf4-a065-4066-8472-fe6a594a1bdd` is the `app_id`; do not include `/workflow`. Use:

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --app-id bdec9bf4-a065-4066-8472-fe6a594a1bdd \
  --server-url <your_agentguard_server_url> \
  --console-url <your_agentguard_console_url>
```

The script generates:

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

The script enables `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true` by default, so the same configuration covers both legacy Agent Chat and Workflow/Chatflow.

### 3. Start Dify With AgentGuard

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

### 4. Configure Rules In AgentGuard

Open the AgentGuard console:

```text
<your_agentguard_console_url>
```

Refresh the Agents list. Synced Dify agents appear with IDs like:

```text
dify-agent-chat:<app_id>
<app_id>:<workflow_id>
```

Open an agent to inspect its tool catalog and configure rules before the app runs. Common tool sources include:

- Dify tools enabled in legacy Agent Chat apps.
- Real Tool nodes in Workflow/Chatflow apps.
- Executable Workflow/Chatflow nodes such as Code, HTTP Request, Knowledge Retrieval, Template Transform, Document Extractor, Variable Aggregator, List Operator, and Datasource.

LLM, Agent, Question Classifier, and Parameter Extractor nodes are not registered as frontend tools; they emit `llm_input` / `llm_output` events at runtime. If/Else, Human Input, Iteration, Loop, Start, End, and Answer are logic/control-flow nodes and are not hooked directly.

### 5. Run Dify And Verify Traces

Run an Agent Chat or Workflow/Chatflow app in Dify. A run with LLM and tool calls usually emits:

```text
llm_input
llm_output
tool_invoke
tool_result
```

Legacy Agent Chat sessions are usually message-scoped. Workflow/Chatflow runs are grouped into one AgentGuard session, with node ID, node execution ID, node type, and node title stored in event metadata.

## Adapter Behavior

The unified integration installs both adapters:

```python
from agentguard.adapters.agent.dify_agent_chat import install_dify_agent_chat_adapter
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_agent_chat_adapter()
install_dify_adapter()
```

The Agent Chat adapter patches:

- `core.app.apps.agent_chat.app_runner.AgentChatAppRunner.run`
- `core.agent.base_agent_runner.BaseAgentRunner._init_prompt_tools`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

The Workflow adapter patches:

- `core.workflow.node_factory.DifyNodeFactory.create_node`
- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`
- `core.tools.tool_engine.ToolEngine.generic_invoke`
- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

Tool deny / sanitize behavior:

- If `tool_invoke` is denied, the adapter does not call the real tool and returns a Dify-compatible blocked / pending observation.
- If `tool_result` is denied or sanitized, the adapter returns a safe observation without breaking Dify's native message-record flow.

## Supported Scope

Validated support currently includes:

- Dify 1.15.x local source deployment.
- Legacy `agent-chat` apps.
- LLM, Agent, Tool, Question Classifier, Parameter Extractor, and executable nodes in Workflow/Chatflow apps.
- LLM calls, LLM outputs, tool calls, and tool results inside Agent Chat / Workflow/Chatflow runtime.
- Pre-run agent / tool catalog sync for configuring rules in the AgentGuard frontend.

Not covered yet:

- Logic or control-flow nodes themselves, such as If/Else, Human Input, Iteration, Loop, Start, End, and Answer.
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
      AGENTGUARD_DIFY_APP_IDS: ""
      AGENTGUARD_DIFY_NODE_IDS: ""
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
      AGENTGUARD_DIFY_APP_IDS: ""
      AGENTGUARD_DIFY_NODE_IDS: ""
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

### Dify Agents Do Not Appear In AgentGuard

Check:

- You are logged in to the correct AgentGuard console.
- Dify containers can reach `AGENTGUARD_SERVER_URL`.
- `PYTHONPATH` includes the bootstrap and AgentGuard client paths.
- Both `api` and `worker` mount AgentGuard and the bootstrap directory.
- `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true` is set to cover legacy Agent Chat apps.
- If `AGENTGUARD_DIFY_APP_IDS` is set, the current app id is in the list; use only the UUID, not `/workflow`.
- If legacy/debug `AGENTGUARD_DIFY_NODE_IDS` is configured, make sure you are validating the old Agent-node path; normal workflow/chatflow integration does not need node IDs.

### Confirm Adapter Installation

Check Dify `api` logs:

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs api \
  | rg "AgentGuard Dify Agent Chat adapter status|AgentGuard Dify workflow adapter status"
```

`patched: True` means the hook was installed.
