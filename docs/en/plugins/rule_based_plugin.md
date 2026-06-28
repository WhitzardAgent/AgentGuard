# rule_based_plugin

`rule_based_plugin` is a built-in server plugin designed for rule-configured tool-call protection. Users write or generate DSL policies, and the plugin evaluates those rules against the current tool invocation and recent session trajectory. When a rule matches, it can either return a fixed decision such as `ALLOW` or `DENY` directly, or escalate into `HUMAN_CHECK` or `LLM_CHECK`. In other words, a matched rule can either decide the outcome itself, ask a human reviewer to decide whether the final result should be allow or deny, or ask an LLM to make that allow-or-deny judgment based on the current context.

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

If you want `LLM_CHECK` rules to be decided by a real LLM reviewer, add reviewer settings directly in the plugin `env` block. The `env` object here means the plugin config fields themselves, so users should fill concrete values directly. The plugin reads these `env` keys: `llm_backend`, `llm_model`, `llm_base_url`, `llm_api_key`, `llm_timeout_s`, and `llm_trace_max_steps`.

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
            "llm_model": "gpt-5.5",
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

In other words, this `env` block should contain the actual reviewer configuration values, not references like `$AGENTGUARD_LLM_MODEL`. If no real reviewer endpoint is configured, the server falls back to its non-networked heuristic path instead of calling a remote LLM.

Policy writing and configuration details:

- [Visual Policy Configuration](../policies/quick_config.md)
- [Policy DSL Structure](../policies/dsl_basic_structure.md)
