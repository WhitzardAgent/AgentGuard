# Openclaw

## 概览

Openclaw 是一个 JavaScript 侧的接入方式，它把 OpenClaw 的插件 hook 映射到 AgentGuard 已有的运行时 phase 上。

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
- `policy`
- `auditPath`
- `remoteUnavailableMode`

`toolCapabilities` 是可选项。只有在你明确想提供一份“工具名 -> capability”的映射时才需要设置它，而不是把 OpenClaw 默认工具做一份不完整的复制。

如果配置了远端 AgentGuard server，这个 adapter 还会：

- 自动注册每个新 session
- 上报一组基础的内置 OpenClaw tool 清单

这样即使在较老的 OpenClaw 版本里没有包装后的 tool metadata，AgentGuard 仍然能拿到一份有意义的工具清单。

## 测试

可以用下面的命令运行 adapter bridge 的测试：

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
