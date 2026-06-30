# Dify Workflow Agent 节点

本文介绍如何把本地部署的 Dify Workflow Agent 节点接入 AgentGuard。这里默认你会拉取 Dify 源码，在本地用 Docker Compose 运行 Dify，并且已经在 Dify 页面里构造好了自己的 Workflow。

## Quick Start

下面用最短路径启动一个已经接入 AgentGuard 的本地 Dify Workflow Agent 节点。

假设：

- AgentGuard 源码在 `/path/to/AgentGuard`
- Dify 源码在 `/path/to/dify`
- Dify 使用 Docker Compose 本地部署
- 你的 Dify Workflow 中已经有一个 Agent 节点，工具配置在这个 Agent 节点内部
- 你已经拿到 AgentGuard server 地址和 API key

### 1. 准备 AgentGuard server 地址

用户侧只需要运行 AgentGuard client adapter，不需要运行 AgentGuard server 或前端。AgentGuard server / 前端控制台通常由 AgentGuard 服务方统一部署。

你需要拿到两个地址：

```text
AGENTGUARD_SERVER_URL=https://<your-agentguard-server>
AGENTGUARD_CONSOLE_URL=https://<your-agentguard-console>
```

其中 `AGENTGUARD_SERVER_URL` 必须是 Dify `api` / `worker` 容器可以访问的服务端 API 地址。`AGENTGUARD_CONSOLE_URL` 是用户登录后查看运行记录的前端地址。

本地开发或自测时，你也可以临时在本机启动 AgentGuard server：

```bash
cd /path/to/AgentGuard
./scripts/start.sh --build -d
```

这时本地测试地址通常是：

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
AGENTGUARD_CONSOLE_URL=http://127.0.0.1:38008/agents.html
```

### 2. 确认 Dify 使用 legacy Agent 路径

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

修改 `.env` 后需要重启 Dify 相关容器。

### 3. 获取 app_id

打开 Dify workflow 页面，浏览器 URL 通常类似：

```text
http://127.0.0.1/app/ce0aa322-1f3f-4ab9-8329-3af8588c7480/workflow
```

其中：

```text
ce0aa322-1f3f-4ab9-8329-3af8588c7480
```

就是 `app_id`。

### 4. 获取 Agent 节点 node_id

Dify 页面上不一定直接显示节点 ID。下面给出三种方法，推荐优先使用方法 A。

#### 方法 A：从 Dify 数据库查询，推荐

在 Dify 的 `docker` 目录执行：

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

如果你的 Dify 数据库用户名或库名不是默认值，把 `postgres` 和 `dify` 改成 `.env` 里的 `DB_USERNAME` 和 `DB_DATABASE`。

输出示例：

```text
    node_id    | title | type
---------------+-------+-------
 1782713638856 | Agent | agent
```

这里的 `1782713638856` 就是 Agent 节点的 `node_id`。

#### 方法 B：从浏览器 Network 里的 workflow draft 获取

1. 打开 Dify workflow 页面。
2. 打开浏览器开发者工具。
3. 进入 Network 面板。
4. 刷新页面，搜索请求：

```text
/console/api/apps/<app_id>/workflows/draft
```

5. 在返回 JSON 里找到：

```text
graph.nodes
```

Agent 节点通常长这样：

```json
{
  "id": "1782713638856",
  "data": {
    "type": "agent",
    "title": "Agent"
  }
}
```

其中 `id` 就是 `node_id`。

如果你已经登录 Dify，也可以在浏览器 Console 里运行：

```javascript
fetch('/console/api/apps/<your_app_id>/workflows/draft')
  .then(r => r.json())
  .then(j => console.table(
    (j.graph?.nodes || [])
      .filter(n => n.data?.type === 'agent')
      .map(n => ({
        node_id: n.id,
        title: n.data?.title,
        type: n.data?.type,
      }))
  ))
```

#### 方法 C：从 workflow 运行事件里找

运行一次 workflow，打开浏览器 Network，找到 workflow run 的 SSE 请求。在事件流里搜索：

```text
node_started
```

你会看到类似：

```json
{
  "event": "node_started",
  "data": {
    "node_id": "1782713638856",
    "node_type": "agent"
  }
}
```

这里的 `node_id` 就是 Agent 节点 ID。

### 5. 生成 Dify 接入文件

回到 AgentGuard 目录，运行：

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

本地自测时可以使用：

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --app-id <your_app_id> \
  --node-id <your_agent_node_id> \
  --server-url http://host.docker.internal:38080 \
  --policy /agentguard/rules/block_email_send.json \
  --console-url http://127.0.0.1:38008/agents.html
```

脚本会生成：

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

### 6. 启动接入 AgentGuard 的 Dify

```bash
cd /path/to/dify/docker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d --force-recreate api worker
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

如果 Dify 其他服务还没启动，也可以直接启动完整 Dify：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml up -d
```

### 7. 运行 workflow 并在 AgentGuard 前端查看

打开 Dify：

```text
http://127.0.0.1
```

运行你的 workflow。运行完成后打开 AgentGuard：

```text
<your_agentguard_console_url>
```

登录你的 AgentGuard 控制台，刷新 Agent 列表。如果 Agent 节点调用过工具，你应该能看到 Dify agent 和对应工具。进入 Runtime 页面后可以查看该 agent 的运行记录和事件序列。

## 当前支持范围

当前 Dify adapter 已验证支持：

- Dify 1.15.x 本地源码部署。
- `ENABLE_AGENT_V2=false` 的 legacy Workflow Agent 路径。
- Workflow 图中包含 Agent 节点，例如 `开始 -> Agent -> 结束`。
- 工具配置在 Agent 节点内部，由 Agent 根据 LLM 输出选择工具。
- Agent 节点内部的 LLM 调用、LLM 返回、工具调用、工具返回会转换成 AgentGuard RuntimeEvent，并发送到 AgentGuard server。
- Agent 节点中实际调用过的工具会自动上报到 AgentGuard server，前端 `agents.html` 可以发现对应 Dify agent。

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

推荐部署方式不修改 Dify 源码，而是用 `sitecustomize.py` 在 Dify Python 进程启动时自动执行这段安装逻辑。

legacy Workflow Agent 同进程路径：

- `core.workflow.nodes.agent.agent_node.AgentNode._run`
- `core.model_manager.ModelInstance.invoke_llm`
- `core.tools.tool_engine.ToolEngine.agent_invoke`

plugin daemon 反向调用路径：

- `core.plugin.backwards_invocation.model.PluginModelBackwardsInvocation.invoke_llm`
- `core.plugin.backwards_invocation.tool.PluginToolBackwardsInvocation.invoke_tool`

第二组 hook 很重要。Dify legacy Agent 经常会通过 plugin daemon 调用 `/invoke/llm` 和 `/invoke/tool`，真实 LLM 和工具执行发生在 API 进程里，而不是最初进入 Agent 节点的 worker 进程里。

## 安全检查行为

LLM 调用前：生成 `llm_input`，payload 中包含 Dify 已构造好的 prompt messages。

LLM 调用后：生成 `llm_output`，payload 只包含 `output`、`thought`、`final_output`；纯工具调用时三者为 `null`。

工具调用前：生成 `tool_invoke`，payload 中包含工具名和工具参数。如果 AgentGuard 返回 deny 或 pending，adapter 不会调用真实工具，而是向 Agent 返回可读的 blocked/pending observation。

工具调用后：生成 `tool_result`，payload 中包含工具返回文本。如果结果阶段返回 deny 或 sanitize，adapter 会把处理后的 observation 返回给 Agent。

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

在 Dify 页面中创建或打开你的 Workflow，并确认图中有一个 Agent 节点，工具配置在 Agent 节点内部。

## 获取 app_id 和 node_id

你需要记录两个 ID，用于只接入指定 workflow 的指定 Agent 节点，避免影响同一个 Dify 实例里的其他应用。

`app_id` 可以从 Dify workflow 页面的 URL 获取，`node_id` 推荐从本地 Dify 数据库的 workflow draft graph 中查询。详细步骤见本文开头的 [Quick Start](#quick-start)。

## 推荐：生成部署接入文件

AgentGuard 提供脚本自动生成 Dify 接入所需的 `sitecustomize.py` 和 Compose override：

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

如果需要接入多个 workflow 或多个 Agent 节点，可以重复传入参数或使用逗号分隔：

```bash
scripts/setup-dify-agentguard.sh \
  --dify-dir /path/to/dify \
  --app-id app_id_1 --app-id app_id_2 \
  --node-id node_id_1 --node-id node_id_2 \
  --server-url <your_agentguard_server_url>
```

脚本会生成：

```text
/path/to/dify/agentguard-dify-bootstrap/sitecustomize.py
/path/to/dify/docker/docker-compose.agentguard.yml
```

生成的 `sitecustomize.py` 只包含 adapter 安装逻辑：

```python
from agentguard.adapters.agent.dify import install_dify_adapter

install_dify_adapter()
```

生成的 Compose override 会把 AgentGuard 源码和 bootstrap 目录挂载到 Dify `api`、`worker`，并设置：

- `AGENTGUARD_ENABLED`
- `AGENTGUARD_SERVER_URL`
- `AGENTGUARD_API_KEY`
- `AGENTGUARD_POLICY`
- `AGENTGUARD_DIFY_APP_IDS`
- `AGENTGUARD_DIFY_NODE_IDS`
- `PYTHONPATH`

## 准备 AgentGuard server 连接信息

用户侧不需要启动 AgentGuard server。你只需要确认 Dify 容器可以访问 AgentGuard 服务方提供的 server API 地址：

```text
AGENTGUARD_SERVER_URL=<your_agentguard_server_url>
```

本地开发或自测时，如果 AgentGuard server 运行在宿主机 `38080` 端口，Dify compose 中可以使用：

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
```

Linux Docker 环境中，生成的 override 会包含：

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

## 验证接入

运行你的 Dify workflow，然后打开 AgentGuard 服务方提供的控制台：

```text
<your_agentguard_console_url>
```

登录后刷新 Agent 列表。如果 Agent 节点调用过工具，你应该能看到 Dify agent 和对应工具。进入 Runtime 页面后可以查看该 agent 的运行记录和事件序列。

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
4. 重新运行 `scripts/setup-dify-agentguard.sh`，或手动更新 `docker-compose.agentguard.yml` 中的 `AGENTGUARD_DIFY_APP_IDS` 和 `AGENTGUARD_DIFY_NODE_IDS`。
5. 重启 `api` 和 `worker`，再重启 `nginx`。
6. 运行 workflow，在 AgentGuard 服务方提供的控制台查看 agent、工具和事件。

整个过程不需要修改 Dify 源码。

## 常见问题

### Dify 前端卡在骨架屏或 API 返回 502

如果你重建过 `api` 容器，nginx 可能仍然缓存旧的 upstream IP。执行：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml restart nginx
```

### AgentGuard 前端没有看到 Dify agent

检查：

- 你是否登录了正确的 AgentGuard 控制台地址。
- Dify 容器是否能访问 `AGENTGUARD_SERVER_URL`。
- Dify workflow 是否真的运行到了目标 Agent 节点。
- Agent 节点是否实际调用过工具。工具目录是在首次工具调用时上报的。
- `AGENTGUARD_DIFY_APP_IDS` 和 `AGENTGUARD_DIFY_NODE_IDS` 是否匹配当前 workflow。
- `PYTHONPATH` 是否包含 bootstrap 和 AgentGuard client 路径。
- `api` 和 `worker` 是否都挂载了 AgentGuard 和 bootstrap。

### workflow 运行成功但 server 没收到事件

检查 Dify 容器是否能访问 AgentGuard server：

```bash
docker compose -f docker-compose.yaml -f docker-compose.agentguard.yml exec api \
  curl -sS <your_agentguard_server_url>/health
```

如果 AgentGuard server 需要认证或健康检查路径不同，请使用服务方提供的测试命令或健康检查地址。

### 工具 deny 后 Dify 会发生什么

`tool_invoke` 阶段如果被 deny 或 pending，adapter 不会调用真实工具，而是返回一段 Agent 可读的 blocked/pending observation。Agent 后续会把这段 observation 当作工具观察结果继续推理。
