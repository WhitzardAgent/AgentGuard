# Dify 统一接入

本文介绍如何把本地 Docker Compose 部署的 Dify 接入 AgentGuard。当前 adapter 已基本覆盖两类主流 Dify 智能体：

- **旧版 Agent Chat app**：URL 通常类似 `/app/<app_id>/configuration`，Dify app mode 是 `agent-chat`，由 `dify_agent_chat` adapter 接入。
- **Workflow / Chatflow app**：URL 通常类似 `/app/<app_id>/workflow`，由 `dify` workflow adapter 接入，覆盖图里的 LLM、Agent、Tool 和执行型节点。

两类接入共用同一套部署方式，不需要修改 Dify 源码。推荐在 Dify `api` / `worker` Python 进程启动时通过 `sitecustomize.py` 自动安装 AgentGuard adapter，并用 Docker Compose override 挂载 AgentGuard client。

接入后，AgentGuard 前端可以在运行前看到已同步的 Dify agent / 工具目录，用来提前配置安全规则。运行时事件仍会按实际调用持续写入 trace。

## Quick Start：统一接入 Dify

假设：

- AgentGuard 源码在 `/path/to/AgentGuard`
- Dify 源码在 `/path/to/dify`
- Dify 使用 Docker Compose 本地部署
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

### 2. 生成接入文件

如果希望接入当前 Dify 实例里的所有旧版 Agent Chat 和 Workflow/Chatflow app，不传 `--app-id`：

```bash
cd /path/to/AgentGuard
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --server-url <your_agentguard_server_url> \
  --api-key <your_agentguard_api_key> \
  --policy dify_default \
  --console-url <your_agentguard_console_url>
```

本地自测示例：

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --server-url http://host.docker.internal:38080 \
  --console-url http://127.0.0.1:38008/agents.html
```

如果只想接入指定 app，可以从 Dify URL 中取 `app_id`：

```text
http://127.0.0.1/app/bdec9bf4-a065-4066-8472-fe6a594a1bdd/workflow
```

其中 `bdec9bf4-a065-4066-8472-fe6a594a1bdd` 是 `app_id`，不要带 `/workflow`。指定过滤时使用：

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --app-id bdec9bf4-a065-4066-8472-fe6a594a1bdd \
  --server-url <your_agentguard_server_url> \
  --console-url <your_agentguard_console_url>
```

脚本会生成：

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

脚本默认开启 `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true`，因此同一份配置会同时覆盖旧版 Agent Chat 和 Workflow/Chatflow。

### 3. 启动接入后的 Dify

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

### 4. 在 AgentGuard 前端配置规则

打开 AgentGuard 控制台：

```text
<your_agentguard_console_url>
```

刷新 Agent 列表。已同步的 Dify agent 会以不同 `agent_id` 出现：

```text
dify-agent-chat:<app_id>
<app_id>:<workflow_id>
```

进入对应 agent 后，可以先查看工具目录并配置规则。常见工具来源包括：

- 旧版 Agent Chat 中实际启用的 Dify tool。
- Workflow/Chatflow 中的真实 Tool 节点。
- Workflow/Chatflow 中的执行型节点，例如 Code、HTTP Request、Knowledge Retrieval、Template Transform、Document Extractor、Variable Aggregator、List Operator、Datasource。

LLM、Agent、Question Classifier、Parameter Extractor 节点不会作为工具注册到前端；它们会在运行时产生 `llm_input` / `llm_output` 事件。If/Else、Human Input、Iteration、Loop、Start、End、Answer 等逻辑或控制流节点本身不 hook。

### 5. 运行 Dify 并验证 trace

在 Dify 里运行 Agent Chat 或 Workflow/Chatflow。一次包含 LLM 和工具调用的运行通常会产生：

```text
llm_input
llm_output
tool_invoke
tool_result
```

旧版 Agent Chat 的 session 通常按消息维度记录；Workflow/Chatflow 的一次运行会聚合为一个 AgentGuard session，节点 ID、节点执行 ID、节点类型和节点标题会写入事件 metadata。

## Adapter 行为

统一接入文件会同时安装两个 adapter：

```python
from agentguard.adapters.agent.dify_agent_chat import install_dify_agent_chat_adapter
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_agent_chat_adapter()
install_dify_adapter()
```

Agent Chat adapter patch 的 Dify 调用点包括：

- `core.app.apps.agent_chat.app_runner.AgentChatAppRunner.run`
- `core.agent.base_agent_runner.BaseAgentRunner._init_prompt_tools`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

Workflow adapter patch 的 Dify 调用点包括：

- `core.workflow.node_factory.DifyNodeFactory.create_node`
- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`
- `core.tools.tool_engine.ToolEngine.generic_invoke`
- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

工具 deny / sanitize 行为：

- `tool_invoke` 阶段被 deny 时，adapter 不调用真实工具，而是返回 Dify 兼容的 blocked / pending observation。
- `tool_result` 阶段被 deny 或 sanitize 时，adapter 返回安全 observation，避免破坏 Dify 原生消息记录流程。

## 支持范围

当前已验证支持：

- Dify 1.15.x 本地源码部署。
- 旧版 `agent-chat` app。
- Workflow/Chatflow 中的 LLM、Agent、Tool、Question Classifier、Parameter Extractor 和执行型节点。
- Agent Chat / Workflow/Chatflow 内部的 LLM 调用、LLM 返回、工具调用、工具返回。
- 运行前同步 agent / 工具目录，用于提前在 AgentGuard 前端配置规则。

当前暂不覆盖：

- 逻辑或控制流节点本身，例如 If/Else、Human Input、Iteration、Loop、Start、End、Answer。
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
- 是否设置了 `AGENTGUARD_DIFY_AGENT_CHAT_ENABLED=true`，以覆盖旧版 Agent Chat。
- 如果配置了 `AGENTGUARD_DIFY_APP_IDS`，当前 app id 是否在列表中；只填写 UUID，不要带 `/workflow`。
- 如果配置了 legacy/debug 用的 `AGENTGUARD_DIFY_NODE_IDS`，确认你验证的是旧版 Agent 节点路径；常规 workflow/chatflow 接入不需要 node id。

### 如何确认 adapter 安装成功

查看 Dify `api` 日志：

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs api \
  | rg "AgentGuard Dify Agent Chat adapter status|AgentGuard Dify workflow adapter status"
```

看到 `patched: True` 表示 hook 已安装。
