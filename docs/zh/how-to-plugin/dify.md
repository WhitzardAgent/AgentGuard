# Dify Workflow Agent 节点

本文介绍如何把本地部署的 Dify Workflow Agent 节点接入 AgentGuard。这里默认你会拉取 Dify 源码，在本地用 Docker Compose 运行 Dify，并且已经在 Dify 页面里构造好了自己的 Workflow。

## 当前支持范围

当前 Dify adapter 主要支持以下场景：

- Dify 1.15.x 本地源码部署。
- `ENABLE_AGENT_V2=false` 的 legacy Workflow Agent 路径。
- Workflow 图中包含 Agent 节点，例如 `开始 -> Agent -> 结束`。
- 工具配置在 Agent 节点内部，由 Agent 根据 LLM 输出选择工具。
- Agent 节点内部的 LLM 调用、LLM 返回、工具调用、工具返回会转换成 AgentGuard RuntimeEvent。

当前会生成的事件包括：

- `llm_input`
- `llm_output`
- `tool_invoke`
- `tool_result`

如果 LLM 本轮没有自然语言回答，只输出工具调用，那么 `llm_output` 保持为空：

```json
{
  "output": null,
  "thought": null,
  "final_output": null
}
```

工具名称和工具参数会由后续的 `tool_invoke` 事件表达。

## 暂不支持的情况

当前版本不覆盖以下场景：

- 普通 Workflow LLM 节点。
- 独立 Workflow Tool 节点。
- LLM 节点输出后通过条件路由到不同 Tool 节点的图结构。
- Dify Agent App 或 Chat App 的所有路径。
- Dify Agent v2 backend / `dify-agent` 服务路径的完整验证。

adapter 代码里保留了部分 Agent v2 hook，但当前已经验证并推荐使用的是本页描述的 legacy Workflow Agent 节点接入方式。

## 接入原理

Dify 的 Workflow Agent 节点不是用户代码里直接创建的 Python agent 对象，因此不能像 LangChain 那样把某个 agent 实例传给 `guard.attach_langchain(agent)`。

AgentGuard 对 Dify 的接入方式是在 Dify 的 `api` 和 `worker` Python 进程启动时安装 runtime hook：

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

安装后，Dify 正常运行 workflow。AgentGuard adapter 会在运行时 patch Dify 的关键调用点。

legacy Workflow Agent 同进程路径：

- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

plugin daemon 反向调用路径：

- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

第二组 hook 很重要。Dify legacy Agent 经常会通过 plugin daemon 调用 `/invoke/llm` 和 `/invoke/tool`，真实 LLM 和工具执行发生在 API 进程里，而不是最初进入 Agent 节点的 worker 进程里。adapter 会在这些反向调用点创建当前调用所需的 AgentGuard session，并把事件发送到 AgentGuard server。

## 事件和安全检查行为

LLM 调用前：

- 生成 `llm_input`
- payload 中包含 Dify 已构造好的 prompt messages
- server 可以执行 LLM before 阶段的安全检查

LLM 调用后：

- 生成 `llm_output`
- payload 只包含 `output`、`thought`、`final_output`
- 纯工具调用时三者为 `null`

工具调用前：

- 生成 `tool_invoke`
- payload 中包含工具名和工具参数
- 如果 AgentGuard 返回 deny 或 pending，adapter 不会调用真实工具，而是向 Agent 返回可读的 blocked/pending observation

工具调用后：

- 生成 `tool_result`
- payload 中包含工具返回文本
- 如果结果阶段返回 deny 或 sanitize，adapter 会把处理后的 observation 返回给 Agent

## 准备 Dify

拉取 Dify 源码，并使用 Docker Compose 启动。下面假设 Dify 源码在：

```bash
/path/to/dify
```

确保 Dify `.env` 中关闭 Agent v2：

```text
ENABLE_AGENT_V2=false
AGENT_BACKEND_BASE_URL=
```

然后正常启动 Dify：

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml up -d
```

在 Dify 页面中创建或打开你的 Workflow，并确认图中有一个 Agent 节点，工具配置在 Agent 节点内部。

## 获取 app_id 和 node_id

你需要记录两个 ID，用于只接入指定 workflow 的指定 Agent 节点，避免影响同一个 Dify 实例里的其他应用。

`app_id` 通常可以从浏览器 URL 获取：

```text
http://localhost/app/<app_id>/workflow
```

Agent 节点的 `node_id` 可以从 workflow DSL、页面调试日志、workflow run 事件或数据库中获取。运行 workflow 时，Dify 的 SSE 事件中会出现类似：

```json
{
  "event": "node_started",
  "data": {
    "node_id": "1782713638856",
    "node_type": "agent"
  }
}
```

这里的 `1782713638856` 就是 Agent 节点 ID。

## 准备 AgentGuard bootstrap

推荐不修改 Dify 源码，而是通过 `sitecustomize.py` 自动安装 adapter。创建一个本地目录：

```bash
mkdir -p /path/to/agentguard-dify-bootstrap
```

创建 `/path/to/agentguard-dify-bootstrap/sitecustomize.py`：

```python
from agentguard.adapters.agent.dify import install_dify_adapter

print("[AgentGuard] Dify adapter:", install_dify_adapter(), flush=True)
```

Python 启动时会自动 import `sitecustomize`。只要把这个目录放到 Dify `api` 和 `worker` 容器的 `PYTHONPATH` 最前面，就可以在进程启动时安装 hook。

## 准备 compose override

在 Dify 的 `docker` 目录下创建一个 override 文件，例如：

```bash
/path/to/dify/docker/docker-compose.agentguard.yml
```

示例内容：

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

如果需要接入多个 workflow 或多个 Agent 节点，可以用逗号分隔：

```text
AGENTGUARD_DIFY_APP_IDS=app_id_1,app_id_2
AGENTGUARD_DIFY_NODE_IDS=node_id_1,node_id_2
```

如果不配置这两个变量，adapter 会尝试 guard 所有 legacy Workflow Agent 节点。

## 启动 AgentGuard server

确保 AgentGuard server 已经运行，并且 Dify 容器可以访问它。若 AgentGuard server 运行在宿主机 `38080` 端口，compose 中可以使用：

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
```

Linux Docker 环境中，建议保留：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

## 启动接入后的 Dify

在 Dify docker 目录下运行：

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

重启 `nginx` 是为了避免 Dify `api` 容器重建后 IP 变化，而 nginx 仍然连接旧 upstream，导致前端 API 请求出现 `502 Bad Gateway`。

查看启动日志：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs -f api worker
```

你应该看到类似输出：

```text
[AgentGuard] Dify adapter: {
  "enabled": true,
  "patched": true,
  "details": {
    "legacy_api": {
      "patched": true,
      "details": {
        "agent_node": true,
        "model_invoke_llm": true,
        "tool_agent_invoke": true,
        "plugin_backwards_llm": true,
        "plugin_backwards_tool": true
      }
    }
  }
}
```

如果 `agent_v2` 显示 import failed，但 `legacy_api` 的几个 hook 是 true，这对本文场景是正常的。

## 验证事件

运行你的 Dify workflow。若设置了：

```text
AGENTGUARD_DIFY_PRINT_EVENTS=true
```

可以用下面命令查看事件：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml logs -f api worker \
  | rg 'AgentGuard Event|AgentGuard LLMOutput Parsed'
```

一个包含工具调用的 Agent 节点通常会产生类似顺序：

```text
llm_input
llm_output
tool_invoke
tool_result
llm_input
llm_output
```

如果第一轮 LLM 只决定调用工具，`llm_output` 的 payload 会是：

```json
{
  "output": null,
  "thought": null,
  "final_output": null
}
```

随后 `tool_invoke` 会包含工具名和工具参数。

## 新 workflow 的接入流程

当你已经有一个新的 Dify Workflow，并且其中包含 Agent 节点时，接入步骤是：

1. 确认这个 workflow 仍然使用 legacy Workflow Agent 路径，即 `ENABLE_AGENT_V2=false`。
2. 获取新 workflow 的 `app_id`。
3. 获取新 Agent 节点的 `node_id`。
4. 修改 `docker-compose.agentguard.yml`：

```text
AGENTGUARD_DIFY_APP_IDS=<new_app_id>
AGENTGUARD_DIFY_NODE_IDS=<new_agent_node_id>
```

5. 重启 `api` 和 `worker`：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

6. 运行 workflow，检查 AgentGuard server 或容器日志中的事件。

整个过程不需要修改 Dify 源码。

## 常见问题

### 前端卡在骨架屏或 API 返回 502

如果你重建过 `api` 容器，nginx 可能仍然缓存旧的 upstream IP。执行：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

### 没有看到 AgentGuard Event

检查：

- `AGENTGUARD_ENABLED=true`
- `AGENTGUARD_DIFY_PRINT_EVENTS=true`
- `PYTHONPATH` 中包含 bootstrap 和 AgentGuard client 路径
- `api` 和 `worker` 都挂载了 AgentGuard 和 bootstrap
- 启动日志里 `plugin_backwards_llm` 和 `plugin_backwards_tool` 为 true
- 当前 workflow 的 `app_id` 和 Agent 节点 `node_id` 是否匹配过滤条件

### workflow 运行成功但 server 没收到事件

检查 Dify 容器是否能访问 AgentGuard server：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml exec api \
  curl -sS http://host.docker.internal:38080/health
```

如果你的 AgentGuard server 不在宿主机上，改成容器可访问的地址。

### 工具 deny 后 Dify 会发生什么

`tool_invoke` 阶段如果被 deny 或 pending，adapter 不会调用真实工具，而是返回一段 Agent 可读的 blocked/pending observation。Agent 后续会把这段 observation 当作工具观察结果继续推理。

