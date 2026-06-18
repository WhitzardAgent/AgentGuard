# AgentGuard OpenClaw Adapter

This directory contains a third-party OpenClaw plugin that maps four OpenClaw
hooks onto AgentGuard's existing `llm_before`, `llm_after`, `tool_before`, and
`tool_after` phases.

The current v1 wiring is:

- `before_tool_call` -> `tool_before`
- `after_tool_call` -> `tool_after`
- `before_agent_run` -> `llm_before`
- `message_sending` -> `llm_after`

The plugin lives in [`agentguard-plugin`](./agentguard-plugin) and reuses the
current CommonJS AgentGuard JS runtime through a small bridge loaded via
`createRequire(...)`.

## Files

- `agentguard-plugin/index.js`: OpenClaw plugin entry and hook registration
- `agentguard-plugin/bridge.cjs`: phase mapping, per-session state, and
  decision translation
- `agentguard-plugin/agentguard-runtime.cjs`: vendored CJS boundary that reuses
  the existing AgentGuard JS runtime pieces
- `agentguard-plugin/openclaw.plugin.json`: plugin manifest and config schema
- `agentguard-plugin/example-config.json`: sample
  `plugins.entries.agentguard.config`

## Test

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
