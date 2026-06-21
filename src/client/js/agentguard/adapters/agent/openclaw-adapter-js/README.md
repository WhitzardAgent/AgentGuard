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
- `agentguard-plugin/example-config.json`: minimal sample AgentGuard JSON config
- `config/openclaw-agentguard.json`: repo-level sample AgentGuard config for
  `configPath`

## Configuration

Put the AgentGuard runtime settings in a standalone JSON file and point the
OpenClaw plugin at that file instead of embedding AgentGuard settings inline.

Example AgentGuard config:

```json
{
  "serverUrl": "http://127.0.0.1:38080",
  "apiKeyEnvVar": "AGENTGUARD_API_KEY",
  "policy": "builtin",
  "auditPath": "./tmp/openclaw-agentguard-audit.jsonl",
  "remoteUnavailableMode": "fail_closed"
}
```

The repository ships this sample at `config/openclaw-agentguard.json`.

Then merge the plugin wiring below into `~/.openclaw/openclaw.json`:

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

The OpenClaw adapter reads phase wiring from the shared repository config at
`config/plugins.json`, so the AgentGuard JSON file only needs runtime settings
such as `serverUrl`, `apiKeyEnvVar`, `policy`, and `auditPath`.

`toolCapabilities` remains optional. It should only be used when you have a
deliberate tool-name-to-capability mapping to supply, not as a partial list of
OpenClaw defaults.

When a remote AgentGuard server is configured, the adapter also auto-registers
each new session and reports a baseline set of built-in OpenClaw tools so older
OpenClaw versions without wrapped tool metadata still expose a useful tool
inventory to AgentGuard.

## Test

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
