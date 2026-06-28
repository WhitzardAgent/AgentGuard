# rule_based_plugin

`rule_based_plugin` 是一个内置 server plugin，面向基于规则配置的工具调用防护。用户可以手写 DSL 策略，也可以通过 UI 生成策略；该 plugin 会结合当前工具调用和近期 session 轨迹评估这些规则。当规则命中时，它既可以直接返回 `ALLOW` 或 `DENY` 这类固定决策，也可以进入 `HUMAN_CHECK` 或 `LLM_CHECK`。也就是说，命中的规则既可以自己决定最终结果，也可以在该条件成立时转交给人工判断最终是 allow 还是 deny，或者让 LLM 基于当前上下文判断最终是 allow 还是 deny。

在默认 quick start 流程中，`rule_based_plugin` 会作为 `tool_before` 阶段的 server plugin 启用：

```json
{
  "phases": {
    "tool_before": {
      "client": [],
      "server": [{"name": "rule_based_plugin", "env": {}}]
    }
  }
}
```

当你需要用明确、可审计的规则拦截 Shell 命令、非白名单外发请求，或阻止敏感数据流入邮件、HTTP、消息发送等工具时，优先使用这个 plugin。

如果你希望 `LLM_CHECK` 真的交给一个实际的 LLM reviewer 来判断，那么需要在 plugin 的 `env` 里直接填写 reviewer 配置。这里的 `env` 指的是 plugin config 里的字段本身，因此应当直接填写具体值。这个 plugin 会读取这些 `env` 键：`llm_backend`、`llm_model`、`llm_base_url`、`llm_api_key`、`llm_timeout_s` 和 `llm_trace_max_steps`。

```json
{
  "phases": {
    "tool_before": {
      "client": [],
      "server": [
        {
          "name": "rule_based_plugin",
          "env": {
            "llm_backend": "openai",
            "llm_model": "gpt-5.4",
            "llm_base_url": "https://api.openai.com/v1",
            "llm_api_key": "<YOUR_LLM_API_KEY>",
            "llm_timeout_s": 30,
            "llm_trace_max_steps": 5
          }
        }
      ]
    }
  }
}
```

换句话说，这里的 `env` 块应该直接放 reviewer 的实际配置值，而不是写成 `$AGENTGUARD_LLM_MODEL` 这种引用形式。如果没有配置真实的 reviewer endpoint，server 不会去调用远端 LLM，而是会回退到本地的 heuristic 路径。

策略编写与配置说明：

- [可视化策略配置](../policies/quick_config.md)
- [策略 DSL 基本结构](../policies/dsl_basic_structure.md)
