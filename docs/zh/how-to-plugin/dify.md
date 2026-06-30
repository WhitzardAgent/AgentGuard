# Dify Agent 与 Workflow Agent 接入

本文介绍如何把本地 Docker Compose 部署的 Dify agent 接入 AgentGuard。Dify 里常见有两条不同运行路径，接入时要先分清楚：

- **Agent Chat app**：URL 通常类似 `/app/<app_id>/configuration`，Dify app mode 是 `agent-chat`。这种 app 由 `AgentChatAppRunner` 直接运行，使用 `dify_agent_chat` adapter。
- **Workflow Agent 节点**：URL 通常类似 `/app/<app_id>/workflow`，workflow 图里有一个 Agent 节点。它由 workflow runtime 运行，使用 `dify` workflow adapter。

两种接入都不需要修改 Dify 源码。推荐方式是在 Dify `api` / `worker` Python 进程启动时通过 `sitecustomize.py` 安装 AgentGuard adapter，并用 Docker Compose override 挂载 AgentGuard client。

## Quick Start：接入 Dify Agent Chat app

下面是用户已经在 Dify 里开发好一个 Agent Chat app 后的最短接入路径。

假设：

- AgentGuard 源码在 `/path/to/AgentGuard`
- Dify 源码在 `/path/to/dify`
- Dify 使用 Docker Compose 本地部署
- 你的 Dify app URL 类似 `/app/<app_id>/configuration`
- 你已经拿到 AgentGuard server 地址和控制台地址

### 1. 准备 AgentGuard server 地址

用户侧只需要运行 AgentGuard client adapter，不需要自己运行 AgentGuard server 或前端。AgentGuard server / 前端控制台通常由 AgentGuard 服务方统一部署。

你需要拿到：

```text
AGENTGUARD_SERVER_URL=https://<your-agentguard-server>
AGENTGUARD_CONSOLE_URL=https://<your-agentguard-console>
```

本地自测时，可以临时启动 AgentGuard：

```bash
cd /path/to/AgentGuard
./scripts/start.sh --build -d
```

这时 Dify 容器访问宿主机上的 AgentGuard server 通常使用：

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
AGENTGUARD_CONSOLE_URL=http://127.0.0.1:38008/agents.html
```

### 2. 获取 Dify app_id

打开 Agent Chat app 配置页，URL 通常类似：

```text
http://127.0.0.1/app/6680db75-b1ed-4735-b4b4-a76efe1b7b42/configuration
```

其中：

```text
6680db75-b1ed-4735-b4b4-a76efe1b7b42
```

就是 `app_id`。AgentGuard 会把这个 app 映射成：

```text
dify-agent-chat:<app_id>
```

作为控制台里的 `agent_id`。同一个 Dify app 的不同用户会共享这个 `agent_id`，但每条消息会带上 Dify 的 `user_id`、`conversation_id`、`message_id`，server 和策略仍然可以区分用户和消息。

### 3. 生成接入文件

回到 AgentGuard 目录：

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

本地自测示例：

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --agent-chat \
  --app-id 6680db75-b1ed-4735-b4b4-a76efe1b7b42 \
  --server-url http://host.docker.internal:38080 \
  --console-url http://127.0.0.1:38008/agents.html
```

脚本会生成：

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

生成的 `sitecustomize.py` 会安装 Agent Chat adapter 和 workflow adapter。Agent Chat 是否启用由 `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true` 控制。

### 4. 启动接入后的 Dify

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

如果只验证 Agent Chat app，关键进程是 `api`；同时重建 `worker` 对 workflow 测试更方便。

### 5. 验证

打开 Dify，运行你的 Agent Chat app。建议发一条会触发工具的问题。

然后打开 AgentGuard 控制台：

```text
<your_agentguard_console_url>
```

刷新 Agent 列表，查找：

```text
dify-agent-chat:<app_id>
```

一次带工具调用的 Agent Chat 运行通常会产生：

```text
llm_input
llm_output
tool_invoke
tool_result
```

工具目录只会上报本轮运行时实际启用的工具。adapter 使用 Dify `BaseAgentRunner._init_prompt_tools()` 返回的 `tool_instances` 作为注册源，因此 Dify 配置中 `enabled=false` 的工具不会被注册。

## Agent Chat adapter 行为

Agent Chat adapter 安装入口是：

```python
from agentguard.adapters.agent.dify_agent_chat import install_dify_agent_chat_adapter

install_dify_agent_chat_adapter()
```

它 patch 的 Dify 调用点包括：

- `core.app.apps.agent_chat.app_runner.AgentChatAppRunner.run`
- `core.agent.base_agent_runner.BaseAgentRunner._init_prompt_tools`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

运行开始时，adapter 会创建 AgentGuard session：

```text
agent_id   = dify-agent-chat:<app_id>
session_id = <message_id>，没有 message_id 时兜底 task_id
user_id    = Dify application_generate_entity.user_id
```

事件 metadata 会包含：

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

server 侧可以按 `principal.agent_id`、`principal.user_id`、`principal.session_id` 写策略或过滤运行记录。

工具 deny / sanitize 行为：

- `tool_invoke` 阶段被 deny 时，adapter 不调用真实工具，而是返回 Dify 兼容的工具错误 observation。
- `tool_result` 阶段被 deny 或 sanitize 时，adapter 返回安全 observation，避免破坏 Dify 原生消息记录流程。

## Quick Start：接入 Dify Workflow Agent 节点

如果你的 Dify agent 是 workflow 图里的 Agent 节点，按下面路径接入。

### 1. 确认 Dify 使用 legacy Agent 路径

当前已验证支持的是 `ENABLE_AGENT_V2=false` 的 legacy Workflow Agent 节点。检查 Dify 的 `.env`：

```bash
cd /path/to/dify/docker
rg 'ENABLE_AGENT_V2|AGENT_BACKEND_BASE_URL' .env
```

推荐配置：

```text
ENABLE_AGENT_V2=false
AGENT_BACKEND_BASE_URL=
```

### 2. 获取 app_id 和 Agent 节点 node_id

`app_id` 可以从 workflow 页面 URL 获取：

```text
http://127.0.0.1/app/ce0aa322-1f3f-4ab9-8329-3af8588c7480/workflow
```

Dify 页面上不一定直接显示节点 ID。推荐从 Dify 数据库查询：

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

输出中的 `node_id` 就是 Agent 节点 ID。

### 3. 生成接入文件

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

多个 workflow 或多个 Agent 节点可以重复传入参数，或使用逗号分隔。

### 4. 启动与验证

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

运行 workflow 后，在 AgentGuard 控制台刷新 Agent 列表。一个包含工具调用的 Workflow Agent 节点通常会产生：

```text
llm_input
llm_output
tool_invoke
tool_result
llm_input
llm_output
```

## Workflow adapter 行为

Workflow adapter 安装入口是：

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

它 patch 的 legacy Workflow Agent 同进程路径包括：

- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

也会 patch plugin daemon 反向调用路径：

- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

第二组 hook 很重要。Dify legacy Agent 经常会通过 plugin daemon 调用 `/invoke/llm` 和 `/invoke/tool`，真实 LLM 和工具执行发生在 API 进程里，而不是最初进入 Agent 节点的 worker 进程里。

## 支持范围

当前已验证支持：

- Dify 1.15.x 本地源码部署。
- legacy `agent-chat` app。
- `ENABLE_AGENT_V2=false` 的 legacy Workflow Agent 节点。
- Agent Chat / Workflow Agent 内部的 LLM 调用、LLM 返回、工具调用、工具返回。
- 运行开始或首次工具初始化时上报本轮可用工具目录。

当前暂不覆盖：

- 普通 Workflow LLM 节点。
- 独立 Workflow Tool 节点。
- LLM 节点输出后通过条件路由到不同 Tool 节点的图结构。
- Dify Agent v2 backend / `dify-agent` 服务路径的完整验证。

## 手动接入文件

如果不使用脚本，也可以手动创建 `/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py`：

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

再创建 `/path/to/dify/docker/docker-compose.agentguard.yml`：

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

Linux Docker 环境中，`host.docker.internal` 需要 `extra_hosts` 中的 `host-gateway` 映射。

## 常见问题

### Dify 前端卡在骨架屏或 API 返回 502

如果你重建过 `api` 容器，nginx 可能仍然缓存旧的 upstream IP。执行：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

### AgentGuard 前端没有看到 Dify agent

检查：

- 是否登录了正确的 AgentGuard 控制台地址。
- Dify 容器是否能访问 `AGENTGUARD_SERVER_URL`。
- `PYTHONPATH` 是否包含 bootstrap 和 AgentGuard client 路径。
- `api` 和 `worker` 是否都挂载了 AgentGuard 和 bootstrap。
- Agent Chat app 是否设置了 `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true`。
- 如果配置了 `AGENTGUARD_DIFY_APP_IDS`，当前 app id 是否在列表中。
- Workflow Agent 节点是否配置了正确的 `AGENTGUARD_DIFY_NODE_IDS`。
- agent 是否实际初始化或调用过工具；工具目录通常在运行开始或首次工具初始化时上报。

### 如何确认 adapter 安装成功

查看 Dify `api` 日志：

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs api \
  | rg "AgentGuard Dify Agent Chat adapter status|AgentGuard Dify workflow adapter status"
```

看到 `patched: True` 表示 hook 已安装。

### 工具 deny 后 Dify 会发生什么

`tool_invoke` 阶段如果被 deny 或 pending，adapter 不会调用真实工具，而是返回一段 Agent 可读的 blocked/pending observation。Agent 后续会把这段 observation 当作工具观察结果继续推理。
