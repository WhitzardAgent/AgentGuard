# OpenCode

## Overview

OpenCode is a JavaScript-side integration that connects OpenCode's hook system to AgentGuard's runtime phases.

This integration is implemented as an OpenCode plugin under:

- `src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin`

The adapter records and enforces the same four runtime phases used by other AgentGuard integrations:

- LLM input -> `llm_before`
- LLM output -> `llm_after`
- tool invocation -> `tool_before`
- tool result -> `tool_after`

OpenCode agents are named agents in the OpenCode configuration, not separate OpenCode server processes. They can be defined in `opencode.json` or managed with OpenCode's own `opencode agent` commands. AgentGuard does not create OpenCode agents inside OpenCode; it registers the configured OpenCode agent catalog with the AgentGuard server and maps each entry to a canonical AgentGuard agent. A single OpenCode conversation can switch between agents; when that happens, AgentGuard keeps runtime sessions and audit traces separated by `OpenCode session + OpenCode agent`.

## Files

The OpenCode adapter directory contains these key files:

- `agentguard-plugin/index.js`: OpenCode plugin entry and hook registration
- `agentguard-plugin/bridge.cjs`: hook mapping, runtime-auth flow, multi-agent session state, and decision translation
- `agentguard-plugin/agentguard-runtime.cjs`: CommonJS boundary that reuses existing AgentGuard JS runtime pieces
- `agentguard-plugin/example-config.json`: minimal sample AgentGuard JSON config
- `agentguard-plugin/package.json`: local plugin package metadata

## Prerequisites

Before connecting OpenCode, make sure:

- AgentGuard server is running and reachable from the OpenCode process.
- OpenCode is installed and can already call the target model provider.
- You can sign in to the AgentGuard console and generate a user ticket from `User Centre`.
- You know the OpenCode agent names you want AgentGuard to register, for example `agentguard` and `reviewer`.

Do not store a real user ticket in a config file. User tickets are short-lived and consumed once during bootstrap.

## Configure OpenCode

Add named OpenCode agents and the AgentGuard plugin to your OpenCode project config.

For a project-local OpenCode setup, create or update `opencode.json` in the project directory:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "deepseek/deepseek-v4-flash",
  "default_agent": "agentguard",
  "agent": {
    "agentguard": {
      "description": "OpenCode primary agent guarded by AgentGuard.",
      "mode": "primary",
      "model": "deepseek/deepseek-v4-flash"
    },
    "reviewer": {
      "description": "OpenCode reviewer agent guarded by AgentGuard.",
      "mode": "primary",
      "model": "deepseek/deepseek-v4-flash"
    }
  },
  "plugin": [
    [
      "/abs/path/to/AgentGuard/src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin/index.js",
      {
        "configPath": "/abs/path/to/opencode-agentguard.json"
      }
    ]
  ]
}
```

Replace the model and agent names with the values used by your OpenCode project. The `configPath` file is the AgentGuard adapter runtime config described below.

## Configure The AgentGuard Adapter

Start from the sample config:

- `src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin/example-config.json`

Copy it to a project-local path, then edit it for your deployment:

```json
{
  "serverUrl": "http://127.0.0.1:38080",
  "userTicketEnvVar": "AGENTGUARD_USER_TICKET",
  "policy": "builtin",
  "providerInstanceId": "opencode-local",
  "opencodeAgent": "agentguard",
  "opencodeAgents": [
    {
      "id": "agentguard",
      "name": "OpenCode agentguard",
      "description": "Default OpenCode agent guarded by AgentGuard."
    },
    {
      "id": "reviewer",
      "name": "OpenCode reviewer",
      "description": "Reviewer OpenCode agent guarded by AgentGuard."
    }
  ],
  "auditPath": "./tmp/opencode-agentguard-audit.jsonl",
  "remoteUnavailableMode": "fail_closed",
  "remoteTimeoutS": 5,
  "remoteRetries": 1,
  "runtimeRefreshLeadS": 45,
  "windowSize": 8
}
```

Important fields:

- `serverUrl`: AgentGuard server URL as seen by the OpenCode process.
- `userTicketEnvVar`: environment variable that carries the one-time AgentGuard user ticket.
- `providerInstanceId`: stable identifier for this OpenCode deployment. Keep it stable across restarts.
- `opencodeAgent`: fallback/default OpenCode agent name used when a hook event does not include an agent name.
- `opencodeAgents`: catalog of OpenCode agents to register in AgentGuard. Each `id` must match a configured OpenCode agent name.
- `remoteUnavailableMode`: use `fail_closed` when missing runtime auth should block guarded LLM/tool phases.

The adapter reads phase wiring from the shared AgentGuard plugin config at:

- `config/plugins.json`

The OpenCode adapter config only needs runtime connection settings and the OpenCode agent catalog.

## Runtime Auth And Agent Bootstrap

When OpenCode starts, the adapter uses the user ticket to bootstrap the configured OpenCode agent catalog into AgentGuard. The ticket is consumed once. After bootstrap, runtime calls use canonical AgentGuard agent identities plus DPoP runtime session tokens.

The runtime model is:

- One configured OpenCode agent becomes one canonical AgentGuard agent.
- One OpenCode session can create separate AgentGuard runtime sessions for different OpenCode agents.
- Trace ownership follows the currently selected OpenCode agent.
- Runtime session tokens are refreshed before expiration while the OpenCode process stays alive.

For example, if a user starts one OpenCode session with `agentguard`, then switches the same OpenCode session to `reviewer`, new traces should appear under `OpenCode reviewer` in AgentGuard.

## Start OpenCode

Generate a fresh ticket in the AgentGuard console, then start OpenCode with that ticket in the environment:

```bash
export AGENTGUARD_USER_TICKET="agt_xxx"
opencode serve --hostname 0.0.0.0 --port 4096 --print-logs --log-level INFO
```

For long-running development sessions, run OpenCode under a process manager or `tmux`, but still inject a fresh ticket before the process starts. Do not reuse an old ticket after it has expired or has already been consumed.

## End-To-End Test

Open the OpenCode UI, create or select a session, then test each configured agent.

For an LLM-only trace, select `agentguard` and send:

```text
Please reply only with: agentguard e2e ok. Do not call tools.
```

Then switch to `reviewer` in the same or a different OpenCode session and send:

```text
Please reply only with: reviewer e2e ok. Do not call tools.
```

In the AgentGuard runtime console, `Recent Audit` should show `llm_input` and `llm_output` under the matching OpenCode agent.

For tool traces, use an OpenCode flow that actually executes a tool, for example a shell command through the UI or a prompt that causes OpenCode to call `bash`. When the tool is executed, `Recent Audit` should include:

- `tool_invoke`
- `tool_result`

If you switch from `agentguard` to `reviewer` inside the same OpenCode session, new LLM/tool traces should be listed under `OpenCode reviewer`, not under the earlier agent.

## Troubleshooting

### Agents Do Not Appear In AgentGuard

Check:

- `AGENTGUARD_USER_TICKET` was set before OpenCode started.
- The ticket was generated recently and has not been consumed by another process.
- `serverUrl` is reachable from the OpenCode process.
- `opencodeAgents[].id` exactly matches the OpenCode agent names.
- `providerInstanceId` is stable and not accidentally changed between restarts.

### Runtime Authentication Fails

Runtime-auth failures usually mean the bootstrap ticket is missing, expired, already consumed, or the cached agent identity does not match the AgentGuard server state. Generate a new ticket, keep the same `providerInstanceId`, and restart the OpenCode process.

### Traces Appear Under The Wrong Agent

Make sure the running OpenCode process is loading the latest adapter code. The adapter keys runtime state by both OpenCode session and OpenCode agent so that same-session agent switches are attributed to the currently selected agent.

### Duplicate OpenCode Agents In The Console

Duplicate records can appear in development if an older unscoped OpenCode agent was registered before `providerInstanceId` was configured. Current console code hides legacy unscoped duplicates when a scoped OpenCode agent exists. For production deployments, choose a stable `providerInstanceId` before the first bootstrap.

## Test

Run the adapter bridge tests with:

```bash
node --test src/client/js/agentguard/adapters/agent/opencode-adapter-js/agentguard-plugin/bridge.test.cjs
```
