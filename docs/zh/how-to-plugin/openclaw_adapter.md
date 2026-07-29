# OpenClaw

## 概览

OpenClaw 是一个 JavaScript 侧的接入方式，它把 OpenClaw 的插件 hook 映射到 AgentGuard 已有的运行时 phase 上。

这个集成当前是以第三方 OpenClaw 插件的形式实现的，目录位于：

- `src/client/js/agentguard/adapters/agent/openclaw-adapter-js/agentguard-plugin`

当前 v1 的 phase 对应关系是：

- `before_tool_call` -> `tool_before`
- `after_tool_call` -> `tool_after`
- `before_agent_run` -> `llm_before`
- `message_sending` -> `llm_after`

在实现上，这个插件通过 `createRequire(...)` 加载了一个小型 bridge，并复用了现有的 CommonJS 版 AgentGuard JS runtime。

## 关键文件

OpenClaw adapter 目录中比较关键的文件包括：

- `agentguard-plugin/index.js`：OpenClaw 插件入口和 hook 注册位置
- `agentguard-plugin/bridge.cjs`：phase 映射、session 级状态管理和 decision 转换逻辑
- `agentguard-plugin/agentguard-runtime.cjs`：复用现有 AgentGuard JS runtime 的 CommonJS 边界层
- `agentguard-plugin/openclaw.plugin.json`：插件 manifest 和配置 schema
- `agentguard-plugin/example-config.json`：最小 AgentGuard JSON 配置示例
- `config/openclaw-agentguard.json`：仓库里提供的 `configPath` 示例配置

## 配置方法

建议把 AgentGuard runtime 的配置单独放在一个 JSON 文件里，然后让 OpenClaw 插件通过路径引用这个文件，而不是把完整 AgentGuard 配置直接内嵌到 OpenClaw 插件配置里。

一个最小的 AgentGuard runtime 配置示例如下：

```json
{
  "serverUrl": "http://127.0.0.1:38080",
  "apiKeyEnvVar": "AGENTGUARD_API_KEY",
  "userTicketEnvVar": "AGENTGUARD_USER_TICKET",
  "policy": "builtin",
  "auditPath": "./tmp/openclaw-agentguard-audit.jsonl",
  "remoteUnavailableMode": "fail_closed"
}
```

仓库中已经提供了这份示例文件：

- `config/openclaw-agentguard.json`

然后把下面这段插件配置合并进 `~/.openclaw/openclaw.json`：

```json
{
  "plugins": {
    "load": {
      "paths": [
        "/abs/path/to/src/client/js/agentguard/adapters/agent/openclaw-adapter-js/agentguard-plugin"
      ]
    },
    "entries": {
      "agentguard": {
        "enabled": true,
        "hooks": {
          "allowConversationAccess": true
        },
        "config": {
          "configPath": "/abs/path/to/AgentGuard/config/openclaw-agentguard.json"
        }
      }
    }
  }
}
```

## 运行时行为说明

OpenClaw adapter 会从下面这个共享仓库配置中读取 phase wiring：

- `config/plugins.json`

所以 `configPath` 指向的 JSON 文件只需要提供运行时相关配置，比如：

- `serverUrl`
- `apiKeyEnvVar`
- `userTicketEnvVar`
- `policy`
- `auditPath`
- `remoteUnavailableMode`

`toolCapabilities` 是可选项。只有在你明确想提供一份“工具名 -> capability”的映射时才需要设置它，而不是把 OpenClaw 默认工具做一份不完整的复制。

如果配置了远端 AgentGuard server，这个 adapter 还会：

- 启动时强制要求提供 `userTicket` 或 `userTicketEnvVar`，拿不到 ticket 值就直接加载失败
- 基于这个 ticket 创建 AgentGuard DPoP runtime session
- 上报一组基础的内置 OpenClaw tool 清单

这样即使在较老的 OpenClaw 版本里没有包装后的 tool metadata，AgentGuard 仍然能拿到一份有意义的工具清单。

当 OpenClaw runtime 在 `before_agent_start` hook 上提供 `modelProvider`、
`model` 和 `modelBaseUrl` 时，这个 adapter 会把它们转发到 AgentGuard 的
`llm_input.context.metadata.model`，字段名分别为 `provider`、`name`、
`base_url`，以及 `source: "openclaw-runtime"`。这样既不需要改动 prompt
payload，也能直接通过标准 `model.*` 规则上下文做模型相关的策略匹配。

如果需要绑定 AgentGuard 用户，先在 AgentGuard 中创建一个临时用户 ticket，把它导出为 `AGENTGUARD_USER_TICKET`，再启动 OpenClaw。如果已经配置 `serverUrl` 但启动时没有拿到 ticket，这个插件现在会在启动阶段直接失败，不再回退到 legacy identity headers。这个 ticket 只会被消费一次，用来把 OpenClaw runtime agent/session 绑定到 AgentGuard 用户；之后 guard 和 report 请求会使用返回的 DPoP session token，不再发送 legacy identity headers。

## 启动 OpenClaw

先在 AgentGuard 控制台生成一个新的用户 ticket，然后在启动 OpenClaw 的同一个终端里注入环境变量：

```bash
export AGENTGUARD_USER_TICKET="agt_xxx"
openclaw gateway
```

再开一个终端打开 OpenClaw 控制台：

```bash
openclaw dashboard
```

如果你只想打印访问地址而不自动打开浏览器，可以用：

```bash
openclaw dashboard --no-open
```

开发时如果要长期运行，可以把 `openclaw gateway` 放到 `tmux` 或进程管理器里，但仍然必须在进程启动前注入一个新的 ticket。不要复用已经过期或已经被消费过的旧 ticket。

## 测试

可以用下面的命令运行 adapter bridge 的测试：

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
