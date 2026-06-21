# rule_based_plugin

`rule_based_plugin` 是一个内置 server plugin，面向基于规则配置的工具调用防护。用户可以手写 DSL 策略，也可以通过 UI 生成策略；该 plugin 会结合当前工具调用和近期 session 轨迹评估这些规则。当规则命中时，它可以识别对应安全风险，并在工具真正执行前返回 `DENY`、`HUMAN_CHECK` 或 `LLM_CHECK` 等决策。

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

策略编写与配置说明：

- [可视化策略配置](../policies/quick_config.md)
- [策略 DSL 基本结构](../policies/dsl_basic_structure.md)
