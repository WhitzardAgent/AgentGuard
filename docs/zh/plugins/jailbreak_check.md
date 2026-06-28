# jailbreak_check

`jailbreak_check` 是一个内置的 LLM 输入防护 plugin，用来在 prompt 真正到达模型之前识别 prompt injection 和 system prompt 泄露尝试。

和只运行在 server 侧的 `rule_based_plugin` 不同，`jailbreak_check` 同时支持部署在 client 和 server 两侧。两侧的检测逻辑本身相同，主要区别在于它运行的位置，以及你需要把它配置到 plugin config 里的 `client` 还是 `server` 列表。

## Client 侧配置方式

当它作为 client plugin 使用时，`jailbreak_check` 会在智能体本地进程中的 `llm_before` 阶段执行：

```json
{
  "phases": {
    "llm_before": {
      "client": [{"name": "jailbreak_check", "env": {}}],
      "server": []
    }
  }
}
```

当你希望在请求真正发给模型之前、或者在事件发往 server 之前，就先在本地低延迟拦截高风险 prompt 时，优先使用 client 侧方式。命中后，本地 plugin 会立即返回最终 `DENY` 决策。

## Server 侧配置方式

当它作为 server plugin 使用时，同样的检测逻辑会在 AgentGuard Server 的 `llm_before` 阶段执行：

```json
{
  "phases": {
    "llm_before": {
      "client": [],
      "server": [{"name": "jailbreak_check", "env": {}}]
    }
  }
}
```

当你希望统一集中管理、统一审计，或者采用 remote-only 的部署方式时，优先使用 server 侧方式。命中后，server plugin 会在远端决策链路中返回 `DENY` 决策。

## 它会检查什么

`jailbreak_check` 会检查 `llm_input` 事件，并匹配内置的一组可疑 prompt 模板，例如：

- “ignore previous instructions” 这一类 instruction override 模式
- “you are now admin” 或 “act as developer” 这类 role hijack 模式
- `DAN`、`developer mode` 等常见 jailbreak 名称
- 请求泄露 system prompt 的模式
- `[SYSTEM]`、`<<SYS>>`、`assistant:` 这类 delimiter injection 标记
- 可疑的工具滥用或数据外传指令
- 一些简单的社会工程话术模式

一旦命中模板，plugin 会附加相应的 risk signals，并把命中的正则模板记录到 `matched_prompt_templates` 元数据中。

## 默认决策行为

`jailbreak_check` 的默认行为是阻断可疑 prompt。

- 在 client 侧，它返回本地最终 `DENY`。
- 在 server 侧，它在远端 plugin 链路里返回 `DENY`。

两边的目标都一样：在明显恶意、试图绕过约束的 prompt 影响后续行为之前先把它拦下来。

## 什么时候选 client，什么时候选 server

- 如果你要的是**尽可能早地在智能体进程里拦截**，就选 **client 侧**。
- 如果你要的是**集中式运维和远端审计可见性**，就选 **server 侧**。
- 默认建议单侧部署；只有在你明确需要“本地预过滤 + 远端复检”时，才同时放到两侧。
