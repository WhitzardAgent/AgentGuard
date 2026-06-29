# OpenClaw

## Overview

OpenClaw is a JavaScript-side integration that connects OpenClaw's plugin hooks to AgentGuard's existing runtime phases.

This integration is implemented as a third-party OpenClaw plugin under:

- `src/client/js/agentguard/adapters/agent/openclaw-adapter-js/agentguard-plugin`

Its v1 phase mapping is:

- `before_tool_call` -> `tool_before`
- `after_tool_call` -> `tool_after`
- `before_agent_run` -> `llm_before`
- `message_sending` -> `llm_after`

Internally, the plugin reuses the existing CommonJS AgentGuard JS runtime through a small bridge loaded via `createRequire(...)`.

## Files

The OpenClaw adapter directory contains these key files:

- `agentguard-plugin/index.js`: OpenClaw plugin entry and hook registration
- `agentguard-plugin/bridge.cjs`: phase mapping, per-session state, and decision translation
- `agentguard-plugin/agentguard-runtime.cjs`: vendored CommonJS boundary that reuses the existing AgentGuard JS runtime pieces
- `agentguard-plugin/openclaw.plugin.json`: plugin manifest and config schema
- `agentguard-plugin/example-config.json`: minimal sample AgentGuard JSON config
- `config/openclaw-agentguard.json`: repository-level sample AgentGuard config used by `configPath`

## Configuration

Put AgentGuard runtime settings in a standalone JSON file, then point the OpenClaw plugin at that file. Do not embed the full AgentGuard runtime configuration directly inside the OpenClaw plugin entry.

A minimal AgentGuard runtime config looks like this:

```json
{
  "serverUrl": "http://127.0.0.1:38080",
  "apiKeyEnvVar": "AGENTGUARD_API_KEY",
  "policy": "builtin",
  "auditPath": "./tmp/openclaw-agentguard-audit.jsonl",
  "remoteUnavailableMode": "fail_closed"
}
```

The repository already includes this sample at:

- `config/openclaw-agentguard.json`

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

## Runtime behavior

The OpenClaw adapter reads phase wiring from the shared repository config at:

- `config/plugins.json`

So the JSON file referenced by `configPath` only needs runtime settings such as:

- `serverUrl`
- `apiKeyEnvVar`
- `policy`
- `auditPath`
- `remoteUnavailableMode`

`toolCapabilities` is optional. Use it only when you intentionally want to provide a tool-name-to-capability mapping, not as a partial copy of OpenClaw defaults.

When a remote AgentGuard server is configured, the adapter also:

- auto-registers each new session
- reports a baseline set of built-in OpenClaw tools

This helps older OpenClaw versions still expose a useful tool inventory even when wrapped tool metadata is not available.

## Test

Run the adapter bridge test with:

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
