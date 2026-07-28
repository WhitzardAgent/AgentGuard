# OpenCode

## 概览

OpenCode 是一个 JavaScript 侧接入方式，它把 OpenCode 的 hook 系统连接到 AgentGuard 的运行时 phase 上。

这个集成当前以 OpenCode plugin 的形式实现，目录位于：

- `src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin`

这个 adapter 会记录并执行 AgentGuard 统一的四类运行时阶段：

- LLM 输入 -> `llm_before`
- LLM 输出 -> `llm_after`
- 工具调用前 -> `tool_before`
- 工具结果后 -> `tool_after`

OpenCode 的 agent 是 OpenCode 配置中的命名 agent，不是多个独立的 OpenCode server 进程。它们可以写在 `opencode.json` 里，也可以通过 OpenCode 自己的 `opencode agent` 命令管理。AgentGuard 不负责在 OpenCode 内部创建 agent；AgentGuard adapter 会把配置好的 OpenCode agent 清单注册到 AgentGuard server，并把每个 OpenCode agent 映射成一个 canonical AgentGuard agent。同一个 OpenCode 会话里可以切换不同 agent；切换后，AgentGuard 会按照 `OpenCode session + OpenCode agent` 分开维护 runtime session 和审计 trace。

## 关键文件

OpenCode adapter 目录中比较关键的文件包括：

- `agentguard-plugin/index.js`：OpenCode plugin 入口和 hook 注册位置
- `agentguard-plugin/bridge.cjs`：hook 映射、runtime-auth 流程、多 agent session 状态管理和 decision 转换逻辑
- `agentguard-plugin/agentguard-runtime.cjs`：复用现有 AgentGuard JS runtime 的 CommonJS 边界层
- `agentguard-plugin/example-config.json`：最小 AgentGuard JSON 配置示例
- `agentguard-plugin/package.json`：本地 plugin package 元数据

## 前置条件

接入 OpenCode 前，请先确认：

- AgentGuard server 已启动，并且 OpenCode 进程能够访问。
- OpenCode 已安装，并且已经能正常调用目标模型供应商。
- 你可以登录 AgentGuard 控制台，并在 `User Centre` 中生成用户 ticket。
- 你已经确定要注册到 AgentGuard 的 OpenCode agent 名称，例如 `agentguard` 和 `reviewer`。

不要把真实用户 ticket 写进配置文件。用户 ticket 是短时有效的一次性凭证，会在 bootstrap 阶段被消费。

## 配置 OpenCode

在 OpenCode 项目配置中添加命名 agent，并接入 AgentGuard plugin。

对于项目级 OpenCode 配置，可以在项目目录中创建或修改 `opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "deepseek/deepseek-v4-flash",
  "default_agent": "agentguard",
  "agent": {
    "agentguard": {
      "description": "OpenCode primary agent guarded by AgentGuard.",
      "mode": "primary",
      "model": "deepseek/deepseek-v4-flash"
    },
    "reviewer": {
      "description": "OpenCode reviewer agent guarded by AgentGuard.",
      "mode": "primary",
      "model": "deepseek/deepseek-v4-flash"
    }
  },
  "plugin": [
    [
      "/abs/path/to/AgentGuard/src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin/index.js",
      {
        "configPath": "/abs/path/to/opencode-agentguard.json"
      }
    ]
  ]
}
```

请把模型和 agent 名称替换成你的 OpenCode 项目实际使用的值。`configPath` 指向的是下面介绍的 AgentGuard adapter 运行时配置文件。

## 配置 AgentGuard Adapter

建议从下面的示例文件开始：

- `src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin/example-config.json`

把它复制到项目本地路径后，根据你的部署环境修改：

```json
{
  "serverUrl": "http://127.0.0.1:38080",
  "userTicketEnvVar": "AGENTGUARD_USER_TICKET",
  "policy": "builtin",
  "providerInstanceId": "opencode-local",
  "opencodeAgent": "agentguard",
  "opencodeAgents": [
    {
      "id": "agentguard",
      "name": "OpenCode agentguard",
      "description": "Default OpenCode agent guarded by AgentGuard."
    },
    {
      "id": "reviewer",
      "name": "OpenCode reviewer",
      "description": "Reviewer OpenCode agent guarded by AgentGuard."
    }
  ],
  "auditPath": "./tmp/opencode-agentguard-audit.jsonl",
  "remoteUnavailableMode": "fail_closed",
  "remoteTimeoutS": 5,
  "remoteRetries": 1,
  "runtimeRefreshLeadS": 45,
  "skillScan": {
    "enabled": false,
    "discoverDefaults": true,
    "monitor": {
      "enabled": true,
      "debounceMs": 750,
      "pollIntervalMs": 5000
    }
  },
  "mcpScan": {
    "enabled": false,
    "monitor": {
      "enabled": true,
      "debounceMs": 750,
      "pollIntervalMs": 5000
    }
  },
  "windowSize": 8
}
```

关键字段说明：

- `serverUrl`：OpenCode 进程视角下可访问的 AgentGuard server URL。
- `userTicketEnvVar`：保存一次性 AgentGuard 用户 ticket 的环境变量名。
- `providerInstanceId`：当前 OpenCode 部署实例的稳定标识。重启前后应保持不变。
- `opencodeAgent`：fallback/default OpenCode agent 名称。当某些 hook event 没有携带 agent 名称时使用。
- `opencodeAgents`：需要注册到 AgentGuard 的 OpenCode agent 清单。每个 `id` 必须和 OpenCode 配置中的 agent 名称一致。
- `remoteUnavailableMode`：建议在需要强保护时使用 `fail_closed`，runtime auth 不可用时阻断受保护的 LLM/tool 阶段。
- `skillScan` / `mcpScan`：可选的 Skill 和 MCP 清单同步。两者默认关闭，因为上报内容可能包含本地源文件。

OpenCode adapter 会从 AgentGuard 共享 plugin 配置中读取 phase wiring：

- `config/plugins.json`

因此 OpenCode adapter 的配置文件包含运行时连接信息、OpenCode agent 清单，以及可选的 inventory 扫描配置。

## Skill 和 MCP 清单同步

OpenCode 通过 `skill` 工具加载 Skill，并把 MCP 工具作为普通工具定义暴露出来。AgentGuard 已有的工具 hook 会直接对这些调用执行策略和记录 trace。可选的 inventory 扫描器进一步让 AgentGuard server 能看到每个 OpenCode agent 当前可用的 Skill 和 MCP server。

需要显式打开清单上报：

```json
{
  "skillScan": {
    "enabled": true,
    "discoverDefaults": true,
    "monitor": {
      "enabled": true,
      "debounceMs": 750,
      "pollIntervalMs": 5000
    }
  },
  "mcpScan": {
    "enabled": true,
    "monitor": {
      "enabled": true,
      "debounceMs": 750,
      "pollIntervalMs": 5000
    }
  }
}
```

Skill 发现遵循 OpenCode 的本地目录约定：

- 项目中的 `.opencode/skill` 和 `.opencode/skills`
- 项目和全局的 `.claude/skills`、`.agents/skills`
- 全局的 `~/.config/opencode/skill` 和 `~/.config/opencode/skills`
- OpenCode 合并配置中的 `skills.paths`
- 可选的 `skillScan.roots`，相对路径按照 AgentGuard adapter 配置文件所在目录解析

MCP 发现先读取 OpenCode 合并后的 `config.mcp`，并在每次监控刷新时重新读取全局、自定义、项目、`.opencode` 和 inline JSON/JSONC 配置源。因此，即使长期运行的 OpenCode 项目实例还没有重载运行时配置，AgentGuard 清单也能响应 MCP 配置改动。该能力不会让 OpenCode 自动热加载新配置的 MCP server；调用新工具前仍需重建 OpenCode 项目实例或重启 `opencode serve`。扫描器支持本地 `command` 数组和远程 server。环境变量值、header 和 OAuth 配置会在上报前脱敏。通过 OpenCode `tool.definition` hook 或实际 MCP 工具调用观察到的工具定义会关联到对应 server 描述中。带 server 前缀的运行时工具名也会反向归属到 MCP server，使 trace 和规则编辑器都能展示 MCP 元数据。

监控器会在启动时扫描一次；文件系统和 MCP 事件经过 750 ms 去抖合并；同时每 5 秒计算一次签名作为兜底。只有 canonical inventory 发生变化时才上报，删除后也会发送空快照。inventory 使用独立的 runtime state，不复用 trace buffer，因此 inventory 请求缓慢或失败不会延迟 LLM/tool trace 上传。

上报每个 agent 前会应用 OpenCode 的 `permission` 和旧版 `tools` 规则。默认会为每个已配置的 OpenCode agent 分别上报 inventory。可选的 `agentIds` 会把该 inventory 类型的所有上报限制到固定子集，只有明确需要这种限制时才应配置。

inventory 可能包含本地 Skill 或 MCP 源码。只有在信任 AgentGuard server 时才应启用。扫描器默认限制单文件大小、文件数量和总字节数；可通过 `maxFileBytes`、`maxFilesPerSkill`、`maxTotalBytesPerSkill`、`maxFilesPerServer` 和 `maxTotalBytesPerServer` 调整。

## Runtime Auth 和 Agent Bootstrap

OpenCode 启动时，adapter 会使用用户 ticket 把配置好的 OpenCode agent 清单 bootstrap 到 AgentGuard。这个 ticket 会被消费一次。bootstrap 之后，运行时请求会使用 canonical AgentGuard agent 身份和 DPoP runtime session token。

运行时模型如下：

- 一个配置好的 OpenCode agent 会对应一个 canonical AgentGuard agent。
- 一个 OpenCode session 可以为不同 OpenCode agent 创建不同的 AgentGuard runtime session。
- trace 归属跟随当前选中的 OpenCode agent。
- OpenCode 进程保持运行时，runtime session token 会在过期前自动刷新。

例如，用户在一个 OpenCode session 中先使用 `agentguard`，随后切换到 `reviewer`，新的 trace 应该出现在 AgentGuard 的 `OpenCode reviewer` 下。

## 启动 OpenCode

先在 AgentGuard 控制台生成新的 ticket，然后把 ticket 放到环境变量中启动 OpenCode：

```bash
export AGENTGUARD_USER_TICKET="agt_xxx"
opencode serve --hostname 0.0.0.0 --port 4096 --print-logs --log-level INFO
```

`opencode serve` 是 headless server，项目实例采用惰性创建。进程刚启动时不会立即加载项目级 `opencode.json` 及其插件；只有 UI 或 API 客户端请求该项目后才会加载。若需要 AgentGuard 立即 bootstrap 并上报清单，请在另一个终端主动请求一次项目配置：

```bash
curl --noproxy '*' -fsSG \
  --data-urlencode 'directory=/absolute/path/to/opencode-project' \
  http://127.0.0.1:4096/config >/dev/null
```

如果是在项目目录直接运行交互式 `opencode` 客户端，客户端启动过程会加载当前项目，因此这个额外请求只适用于 headless `serve`。

开发阶段如果需要长期运行，可以用进程管理器或 `tmux` 启动 OpenCode，但仍然需要在进程启动前注入新的 ticket。不要复用已经过期或已经被消费过的旧 ticket。

## 端到端测试

打开 OpenCode UI，创建或选择一个 session，然后分别测试每个配置好的 agent。

测试 LLM-only trace 时，先选择 `agentguard` 并发送：

```text
Please reply only with: agentguard e2e ok. Do not call tools.
```

然后在同一个或另一个 OpenCode session 中切换到 `reviewer`，发送：

```text
Please reply only with: reviewer e2e ok. Do not call tools.
```

在 AgentGuard runtime 控制台中，`Recent Audit` 应该能在对应 OpenCode agent 下看到 `llm_input` 和 `llm_output`。

测试工具 trace 时，请使用会真正执行工具的 OpenCode 流程，例如通过 UI 执行 shell 命令，或者让 OpenCode 调用 `bash` 工具。工具真正执行后，`Recent Audit` 应该包含：

- `tool_invoke`
- `tool_result`

如果你在同一个 OpenCode session 中从 `agentguard` 切换到 `reviewer`，新的 LLM/tool trace 应该进入 `OpenCode reviewer`，而不是继续进入之前的 agent。

## 排障

### AgentGuard 中看不到 OpenCode agent

请检查：

- `AGENTGUARD_USER_TICKET` 是否在 OpenCode 启动前已经设置。
- ticket 是否刚刚生成，且没有被其他进程消费。
- OpenCode 进程是否能访问 `serverUrl`。
- `opencodeAgents[].id` 是否和 OpenCode agent 名称完全一致。
- `providerInstanceId` 是否稳定，没有在重启之间被意外修改。

### Runtime Authentication Failed

runtime-auth 失败通常意味着 bootstrap ticket 缺失、过期、已经被消费，或者本地缓存的 agent identity 和 AgentGuard server 状态不一致。请生成新的 ticket，保持相同的 `providerInstanceId`，然后重启 OpenCode 进程。

### Trace 进入了错误的 agent

请确认正在运行的 OpenCode 进程已经加载了最新 adapter 代码。adapter 会同时使用 OpenCode session 和 OpenCode agent 作为 runtime state key，因此同 session 切换 agent 后，新的 trace 应归属到当前选中的 agent。

### 控制台中出现重复 OpenCode agent

开发过程中，如果曾经在没有配置 `providerInstanceId` 的情况下注册过旧 OpenCode agent，可能出现重复记录。当前 console 会在存在 scoped OpenCode agent 时隐藏旧的 unscoped duplicate。生产部署中建议在第一次 bootstrap 前就确定稳定的 `providerInstanceId`。

## 测试

可以用下面的命令运行 adapter bridge 测试：

```bash
node --test src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin/bridge.test.cjs
```
