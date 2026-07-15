# n8n 统一接入

本文介绍如何把本地 Docker 部署的 n8n 接入 AgentGuard。n8n 的 Agent、LLM、AI Tool 和普通执行节点都由 n8n runtime 在内部创建，因此接入方式和 Dify 类似：不修改 n8n 源码，而是在 n8n Node.js 进程启动时通过 `NODE_OPTIONS=--require ...` 预加载 AgentGuard adapter。

接入后，AgentGuard 前端可以在运行前看到已同步的 n8n workflow / 工具目录，用来提前配置安全规则。运行时事件会继续按实际调用写入 trace。

## Quick Start：接入 n8n

假设：

- AgentGuard 源码在 `/path/to/AgentGuard`
- n8n 使用 Docker 或 Docker Compose 本地部署
- 你已经拿到 AgentGuard server 地址和控制台地址

### 1. 准备 AgentGuard server 地址

n8n 侧只运行 AgentGuard client adapter，不需要自己运行 AgentGuard server 或前端。AgentGuard server / 前端控制台通常由 AgentGuard 服务方统一部署。

你需要拿到：

```text
AGENTGUARD_SERVER_URL=https://<your-agentguard-server>
AGENTGUARD_CONSOLE_URL=https://<your-agentguard-console>
AGENTGUARD_API_KEY=<your-agentguard-api-key>
```

本地自测时，可以临时启动 AgentGuard：

```bash
cd /path/to/AgentGuard
./scripts/start.sh --build -d
```

这时 n8n 容器访问宿主机上的 AgentGuard server 通常使用：

```text
AGENTGUARD_SERVER_URL=http://host.docker.internal:38080
AGENTGUARD_CONSOLE_URL=http://127.0.0.1:38008/agents.html
AGENTGUARD_API_KEY=sk-agentguard-backend-X9m42Vq7Tz8nL3pA6cR0yH5uJ1sWfKdE
```

### 2. 生成接入文件

默认接入当前 n8n 实例里的所有 active / published workflow，不需要配置 workflow 白名单：

```bash
cd /path/to/AgentGuard
scripts/setup-n8n-agentguard.sh \
  --server-url <your_agentguard_server_url> \
  --api-key <your_agentguard_api_key> \
  --policy n8n_default \
  --console-url <your_agentguard_console_url>
```

本地自测示例：

```bash
scripts/setup-n8n-agentguard.sh \
  --server-url http://host.docker.internal:38080 \
  --api-key sk-agentguard-backend-X9m42Vq7Tz8nL3pA6cR0yH5uJ1sWfKdE \
  --console-url http://127.0.0.1:38008/agents.html
```

脚本会生成：

```text
/path/to/AgentGuard/agentguard-n8n-bootstrap/register.cjs
/path/to/AgentGuard/docker-compose.n8n-agentguard.yml
```

### 3. 启动接入后的 n8n

如果你使用 Docker Compose，把生成的 override 文件和你的 n8n compose 文件一起使用：

```bash
docker compose -f docker-compose.yml -f /path/to/AgentGuard/docker-compose.n8n-agentguard.yml up -d --force-recreate n8n
```

如果你使用 `docker run`，保留原来的 n8n volume 和环境变量，并额外加入脚本输出中的 AgentGuard 相关参数。最小本地示例：

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
  -e AGENTGUARD_API_KEY=sk-agentguard-backend-X9m42Vq7Tz8nL3pA6cR0yH5uJ1sWfKdE \
  -e AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true \
  -e AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S=5 \
  -e AGENTGUARD_N8N_DB_PATH=/home/node/.n8n/database.sqlite \
  -e NODE_OPTIONS="--require /agentguard-n8n-bootstrap/register.cjs" \
  -v "$AGENTGUARD_ROOT:/agentguard:ro" \
  -v "$AGENTGUARD_ROOT/agentguard-n8n-bootstrap:/agentguard-n8n-bootstrap:ro" \
  --add-host host.docker.internal:host-gateway \
  docker.n8n.io/n8nio/n8n:2.26.8
```

如果已有 n8n 容器是缺少 `AGENTGUARD_API_KEY` 的旧配置，修改环境变量后必须重建容器。当前本地 AgentGuard server 的 API key 是：

```text
sk-agentguard-backend-X9m42Vq7Tz8nL3pA6cR0yH5uJ1sWfKdE
```

重建后用下面的命令确认 n8n 容器内已经拿到 key：

```bash
docker exec n8n printenv AGENTGUARD_API_KEY
docker logs n8n 2>&1 | rg "agentguard:n8n|agents/register|catalog sync"
```

### 4. 在 AgentGuard 前端配置规则

打开 AgentGuard 控制台：

```text
<your_agentguard_console_url>
```

刷新 Agent 列表。已同步的 n8n workflow 会以如下 `agent_id` 出现：

```text
n8n:<workflow_id>
```

进入对应 agent 后，可以先查看工具目录并配置规则。常见工具来源包括：

- 连接到 Agent 节点的 n8n AI Tool，例如 HTTP Request Tool。
- 普通执行节点，例如 HTTP Request、Code 等。

LLM、Agent、memory、parser、retriever、vector store、trigger 和 If / Switch / Merge 等逻辑控制节点不会作为工具注册到前端；其中 LLM / Agent 运行时仍会产生 `llm_input` / `llm_output` 事件。

### 5. 运行 n8n 并验证 trace

在 n8n 里运行 workflow。一次包含 LLM 和工具调用的运行通常会产生：

```text
llm_input
llm_output
tool_invoke
tool_result
```

AgentGuard session metadata 会记录：

```text
environment=n8n
workflow_id
workflow_name
external_session_id
n8n_session_id
execution_id
node_id
node_name
node_type
```

其中 `external_session_id` 和 `n8n_session_id` 来自 n8n 的稳定会话标识，例如 Chat Trigger / Agent 运行上下文里的 `sessionId`、`chatSessionId` 或 `conversationId`。`execution_id` 是 n8n 每次执行的运行编号，只用于审计和排查，不作为 AgentGuard Runtime Session 的主映射键。

## Adapter 行为

n8n adapter 通过 `NODE_OPTIONS` 预加载：

```text
NODE_OPTIONS=--require /agentguard-n8n-bootstrap/register.cjs
```

bootstrap 里会安装：

```js
const { installN8nAdapter } = require("/agentguard/src/client/js/agentguard/adapters/agent/n8n");
installN8nAdapter();
```

当前 adapter 覆盖的运行时路径包括：

- `@langchain/openai` 的 `ChatOpenAIResponses.prototype.completionWithRetry`
- n8n Agent V3 的 engine request / action 路径
- n8n `WorkflowExecute.prototype.runNode`
- n8n Tools Agent 的 connected tools 获取路径

工具 deny / sanitize 行为：

- `tool_invoke` 阶段被 deny 时，adapter 不调用真实工具，而是返回 n8n 兼容的 blocked / pending result。
- `tool_result` 阶段被 deny 或 sanitize 时，adapter 返回安全 result，避免破坏 n8n 原生执行流程。

工具事件的 `arguments` 尽量保持为真实工具业务参数。对于 n8n AI Tool，优先使用工具节点自身的 `node.parameters`，例如 HTTP Request Tool 的 `url`、`method`、`authentication`、`headers`、`body`、`options` 等。`sessionId`、`toolCallId`、`action`、`chatInput` 等 n8n 运行时消息字段会写入 event metadata，而不会放进工具参数里。

OpenAI Responses API 的 `web_search`、`file_search`、`code_interpreter` 等模型服务商 built-in tools 在模型服务商内部执行，adapter 不生成 `tool_invoke` / `tool_result`。如果请求中启用了这类 built-in tool，只会在 `llm_input.metadata` 中记录：

```text
model_builtin_tools
model_builtin_tools_hooked=false
model_builtin_tools_reason=provider_side_execution
```

## Runtime Session 映射

n8n adapter 在运行时会使用 DPoP 向 AgentGuard server 创建或刷新 Runtime Session。映射关系是：

```text
provider=n8n
agent_id=n8n:<workflow_id>
external_session_id=<n8n sessionId>
```

也就是说，一个 n8n 对话 / 会话应该只对应一个 AgentGuard `ags_n8n_*` Runtime Session。同一个 n8n `sessionId` 下的多次 `llm_input`、`llm_output`、`tool_invoke`、`tool_result` 会复用同一个 AgentGuard Runtime Session，而不是按每次 LLM 输入或每次 workflow execution 创建新 session。

AgentGuard 前端 Runtime Sessions 页面里的 `External Session` 应该显示 n8n 的稳定 `sessionId`，例如：

```text
3343066ded4b4b03babdd605c9bde44e
```

如果看到 `external_session_id` 是 `54`、`55`、`56` 这类递增数字，通常说明仍在使用旧 adapter，或者当前 n8n 运行上下文没有暴露稳定 `sessionId`，adapter 退回到了 n8n `execution_id`。

## 运行前工具目录同步

adapter 会定期扫描 n8n SQLite 数据库中的 active / published workflow，并把工具目录同步到 AgentGuard server。默认扫描间隔为 5 秒：

```text
AGENTGUARD_API_KEY=<your-agentguard-api-key>
AGENTGUARD_N8N_CATALOG_SYNC_ENABLED=true
AGENTGUARD_N8N_CATALOG_SYNC_INTERVAL_S=5
AGENTGUARD_N8N_DB_PATH=/home/node/.n8n/database.sqlite
```

注册 agent 时，adapter 会从 n8n 数据库读取每个 workflow 的 owner email，并以 `provider=n8n + account_email=<workflow_owner_email>` 绑定到 AgentGuard 用户。因此同一个 n8n 容器里可以有多个 n8n 用户；不要用容器环境变量写死某一个 n8n 邮箱。每个 AgentGuard 用户只需要在用户中心绑定自己的 n8n 邮箱。同步成功后，即使 workflow 还没有被运行，AgentGuard 前端也可以看到对应的 `n8n:<workflow_id>` agent 和工具目录。workflow 修改并保存 / 发布后，下一次扫描会自动同步新的工具目录。

## 身份密钥与 DPoP Key 持久化

n8n adapter 会为注册到 AgentGuard 的 workflow agent 使用 agent identity key，并为 Runtime Session 使用 DPoP key。默认情况下，如果没有显式设置 `AGENTGUARD_AGENT_KEY_DIR`，adapter 会把密钥目录设置为：

```text
/home/node/.n8n/agentguard_keys
```

在上面的 Docker 示例里，`n8n_data:/home/node/.n8n` 会同时持久化 n8n 数据库、AgentGuard agent identity key 和 DPoP key。不要把 `/home/node/.n8n` 换成只读挂载；否则容器重启后可能重新生成 key，导致已有 Runtime Session 无法稳定刷新。

如果你显式设置 `AGENTGUARD_AGENT_KEY_DIR` 或 `AGENTGUARD_DPOP_KEY_DIR`，需要确保该目录在 n8n 容器内可写，并且被持久化。

## 支持范围

当前已验证支持：

- n8n 2.26.8 本地 Docker 部署。
- Chat Trigger -> AI Agent -> OpenAI Chat Model 的 LLM 输入输出事件。
- 连接到 Agent 的 n8n AI Tool 工具调用和工具返回。
- 普通 HTTP Request / Code 等执行节点作为工具记录。
- active / published workflow 的运行前工具目录同步。
- `N8N_RUNNERS_ENABLED=true` 下主进程和 task runner 的 adapter 预加载。

当前暂不覆盖：

- 模型服务商内部执行的 built-in tools，例如 OpenAI `web_search`、`file_search`、`code_interpreter`。
- If / Switch / Merge / Trigger / Agent / LLM / memory / parser / retriever / vector store 等节点作为工具事件。
- 非 SQLite 数据库部署下的 workflow catalog 扫描。

## 手动接入文件

如果不使用脚本，也可以手动创建 `/path/to/AgentGuard/agentguard-n8n-bootstrap/register.cjs`：

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

再创建 Docker Compose override：

```yaml
services:
  n8n:
    environment:
      AGENTGUARD_ENABLED: "true"
      AGENTGUARD_ENVIRONMENT: "n8n"
      AGENTGUARD_ROOT: "/agentguard"
      AGENTGUARD_SERVER_URL: "http://host.docker.internal:38080"
      AGENTGUARD_API_KEY: "sk-agentguard-backend-X9m42Vq7Tz8nL3pA6cR0yH5uJ1sWfKdE"
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

Linux Docker 环境中，`host.docker.internal` 需要 `extra_hosts` 中的 `host-gateway` 映射。

## 常见问题

### AgentGuard 前端没有看到 n8n agent

检查：

- 是否登录了正确的 AgentGuard 控制台地址。
- n8n 容器是否能访问 `AGENTGUARD_SERVER_URL`。
- n8n 容器是否设置了和 AgentGuard server 一致的 `AGENTGUARD_API_KEY`；缺少或错误会导致 `/v1/server/agents/register` 被拒绝，AgentGuard 里不会出现 n8n agent。
- `NODE_OPTIONS` 是否包含 `/agentguard-n8n-bootstrap/register.cjs`。
- n8n 容器是否挂载了 AgentGuard 源码目录和 bootstrap 目录。
- `AGENTGUARD_N8N_CATALOG_SYNC_ENABLED` 是否为 `true`。
- `AGENTGUARD_N8N_DB_PATH` 是否指向 n8n 容器内的 SQLite 数据库。
- 当前 n8n workflow owner email 是否已经在 AgentGuard 用户中心绑定。adapter 注册时按 workflow owner email 自动绑定，不应在容器里写死单个邮箱。
- 当前 workflow 是否 active 或 published。

默认不需要设置 workflow 白名单。若容器里残留了旧的 `AGENTGUARD_N8N_WORKFLOW_IDS`，它会让 adapter 只接入指定 workflow。多 agent 场景应删除该变量，然后重新创建 n8n 容器。

### 如何确认 adapter 安装成功

查看 n8n 日志：

```bash
docker logs n8n 2>&1 | rg "agentguard:n8n|catalog sync"
```

看到类似下面的日志表示 hook 已安装，且目录同步已启动：

```text
[agentguard:n8n] adapter installed
[agentguard:n8n] bootstrap status { installed: true, catalog_sync: ... }
[agentguard:n8n] catalog sync complete ...
```

### 修改环境变量后没有生效

Docker 容器环境变量在创建容器时固定。修改 compose 文件后，需要重新创建 n8n 容器：

```bash
docker compose -f docker-compose.yml -f /path/to/AgentGuard/docker-compose.n8n-agentguard.yml up -d --force-recreate n8n
```

如果是 `docker run` 部署，需要删除旧容器后重新运行。

### 同一个 n8n 会话被拆成多个 AgentGuard Runtime Session

正常情况下，同一个 n8n `sessionId` 应该映射到同一个 AgentGuard Runtime Session。先确认 n8n 已经重建容器并加载了最新版 adapter，然后查看 AgentGuard 数据库里的 n8n Runtime Session：

```bash
docker exec agentguard-mysql-1 mysql -uagentguard -pagentguard agentguard -e \
"SELECT session_id, agent_id, provider, external_session_id, status, created_at
 FROM runtime_sessions
 WHERE provider='n8n'
 ORDER BY created_at DESC
 LIMIT 10"
```

期望看到 `external_session_id` 是 n8n 的稳定会话字符串，例如 `3343066ded4b4b03babdd605c9bde44e`。如果这里是递增的 execution id，例如 `54`、`55`、`56`，说明 adapter 没有拿到 n8n `sessionId`，或者 n8n 容器仍在运行旧的接入文件。
