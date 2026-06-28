# 内置插件

AgentGuard 提供了一组面向常见运行时防护需求的内置 plugin。这个页面按运行位置区分当前文档里已经单独说明的内置 plugin。

## 内置 Client Plugins

- [jailbreak_check](jailbreak_check.md)：一个运行在 `llm_before` 阶段的 prompt injection 检测器，可以在智能体本地进程里尽早拦截可疑 prompt。

## 内置 Server Plugins

- [rule_based_plugin](rule_based_plugin.md)：一个仅运行在 server 侧的规则引擎，用于工具调用访问控制和策略评估；它既可以直接返回固定的 `ALLOW` / `DENY`，也可以把命中的情况转入 `HUMAN_CHECK` / `LLM_CHECK`。
- [jailbreak_check](jailbreak_check.md)：同一个 LLM 输入检测器也可以部署在 AgentGuard Server 侧，用于集中式治理和审计。

代码库里还有其他工具型 plugin，但这个页面当前聚焦于已经在公开文档中单独展开说明的内置 plugin。
