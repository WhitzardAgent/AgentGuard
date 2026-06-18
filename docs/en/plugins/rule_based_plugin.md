# rule_based_plugin

`rule_based_plugin` is a built-in server plugin designed for rule-configured tool-call protection. Users write or generate DSL policies, and the plugin evaluates those rules against the current tool invocation and recent session trajectory. When a rule matches, it can identify the security risk and return a decision such as `DENY`, `HUMAN_CHECK`, or `LLM_CHECK` before the tool call executes.

In the default quick-start flow, `rule_based_plugin` is configured as a server plugin in the `tool_before` phase:

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

Use this plugin when you want explicit, auditable rules for cases such as blocking shell commands, preventing non-allowlisted outbound requests, or stopping sensitive data from flowing into email, HTTP, or messaging tools.

Policy writing and configuration details:

- [Visual Policy Configuration](../policies/quick_config.md)
- [Policy DSL Structure](../policies/dsl_basic_structure.md)
