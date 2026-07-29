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
  "userTicketEnvVar": "AGENTGUARD_USER_TICKET",
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
- `userTicketEnvVar`
- `policy`
- `auditPath`
- `remoteUnavailableMode`

`toolCapabilities` is optional. Use it only when you intentionally want to provide a tool-name-to-capability mapping, not as a partial copy of OpenClaw defaults.

When a remote AgentGuard server is configured, the adapter also:

- requires `userTicket` or `userTicketEnvVar` at startup and fails to load if no ticket value is available
- creates an AgentGuard DPoP runtime session from that ticket
- reports a baseline set of built-in OpenClaw tools

This helps older OpenClaw versions still expose a useful tool inventory even when wrapped tool metadata is not available.

When the OpenClaw runtime provides `modelProvider`, `model`, and `modelBaseUrl`
on the `before_agent_start` hook, the adapter forwards them into AgentGuard
`llm_input.context.metadata.model` as `provider`, `name`, `base_url`, and
`source: "openclaw-runtime"`. This keeps the prompt payload unchanged while
making model-aware policy matching available through the standard `model.*`
rule context.

For AgentGuard user binding, create a temporary user ticket in AgentGuard, export it as `AGENTGUARD_USER_TICKET`, then start OpenClaw. If `serverUrl` is configured and the ticket is missing, the plugin now fails fast during startup instead of falling back to legacy identity headers. The ticket is consumed once and binds the OpenClaw runtime agent/session to the AgentGuard user; after that, guard and report requests use the returned DPoP session token instead of legacy identity headers.

## Start OpenClaw

Generate a fresh user ticket in the AgentGuard console, then inject it into the same shell where you start OpenClaw:

```bash
export AGENTGUARD_USER_TICKET="agt_xxx"
openclaw gateway
```

Open the OpenClaw dashboard from another terminal:

```bash
openclaw dashboard
```

If you only want the URL without auto-opening the browser, use:

```bash
openclaw dashboard --no-open
```

For long-running development sessions, run `openclaw gateway` under `tmux` or a process manager, but still inject a fresh ticket before the process starts. Do not reuse an old ticket after it has expired or has already been consumed.

## Test

Run the adapter bridge test with:

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
