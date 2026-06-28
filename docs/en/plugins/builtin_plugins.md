# Builtin Plugins

AgentGuard includes built-in plugins for common runtime protection needs. This page groups the built-in plugins currently documented in this manual by where they run.

## Built-in Client Plugins

- [jailbreak_check](jailbreak_check.md): an `llm_before` prompt-injection detector that can run locally inside the agent process and block suspicious prompts before they reach the model.

## Built-in Server Plugins

- [rule_based_plugin](rule_based_plugin.md): a server-only rule engine for tool-call access control and policy evaluation that can either return fixed `ALLOW` / `DENY` decisions or escalate matched cases to `HUMAN_CHECK` / `LLM_CHECK`.
- [jailbreak_check](jailbreak_check.md): the same LLM-input detector can also run on the AgentGuard server when you want centralized governance and auditing.

Other utility plugins also exist in the codebase, but this page focuses on the built-in plugins that are documented for direct use in the public docs.
