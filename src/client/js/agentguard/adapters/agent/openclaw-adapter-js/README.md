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
  "userTicketEnvVar": "AGENTGUARD_USER_TICKET",
  "policy": "builtin",
  "defaultToolCatalogPath": "./openclaw-default-tools.json",
  "auditPath": "./tmp/openclaw-agentguard-audit.jsonl",
  "remoteUnavailableMode": "fail_closed",
  "skillScan": {
    "enabled": false,
    "roots": [],
    "monitor": true,
    "monitorDebounceMs": 750,
    "monitorPollIntervalMs": 5000
  },
  "mcpScan": {
    "enabled": false,
    "roots": [],
    "configPaths": [],
    "monitor": true,
    "monitorDebounceMs": 750,
    "monitorPollIntervalMs": 5000
  }
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
`config/plugins.json`, and the baseline tool inventory from
`config/openclaw-default-tools.json`. The AgentGuard JSON file can override the
tool catalog via `defaultToolCatalogPath`, which is resolved relative to the
AgentGuard config file when you use `configPath`.

`toolCapabilities` remains optional. It should only be used when you have a
deliberate tool-name-to-capability mapping to supply, not as a partial list of
OpenClaw defaults.

`skillScan` is optional and disabled by default. Set `skillScan.enabled` to
`true` and provide local `skillScan.roots` to scan OpenClaw-compatible skill
directories containing `SKILL.md`; relative roots are resolved against the
AgentGuard config file directory. The adapter keeps the full skill descriptors
locally in bridge state and reports them to the AgentGuard server, while session
metadata only includes a compact scan summary.

When `skillScan.enabled` is true, `skillScan.monitor` is enabled by default.
The monitor runs inside the OpenClaw plugin/client process, watches configured
skill roots, periodically rescans them, and reports changed skill inventories to
AgentGuard without waiting for another agent turn. `monitorDebounceMs` controls
how long filesystem events are coalesced before a rescan, and
`monitorPollIntervalMs` provides a polling fallback for filesystems where
`fs.watch` misses nested edits. If several OpenClaw agents are configured,
`skillScan.agentIds` can restrict which agent IDs receive the reported skill
inventory; otherwise the adapter infers targets from OpenClaw workspace paths
when possible. A running OpenClaw plugin/client process is still required for
the monitor to execute.

`mcpScan` is optional and disabled by default. Set `mcpScan.enabled` to `true`
and provide either `mcpScan.roots` or explicit `mcpScan.configPaths` to scan MCP
server configs such as `.cursor/mcp.json` or OpenClaw MCP config entries. When
enabled, `mcpScan.monitor` is enabled by default. The monitor watches configured
MCP roots and config files, periodically rescans them, and reports changed MCP
inventories to AgentGuard without waiting for another agent turn. Empty
inventories are reported with `sync_inventory=true`, so deleting an MCP from the
client side removes the stale MCP from AgentGuard after the next monitor refresh.
`mcpScan.agentIds` can restrict which OpenClaw agent IDs receive the reported MCP
inventory; otherwise the adapter infers targets from OpenClaw workspace paths
when possible.

When a remote AgentGuard server is configured, every remote OpenClaw session
requires `userTicket` or `userTicketEnvVar`. If `serverUrl` is configured and
no ticket value is available at startup, the plugin fails to load instead of
falling back to legacy identity headers. With a user ticket, the adapter
consumes that ticket through `/v1/server/session/create`, binds the OpenClaw
runtime agent/session to the AgentGuard user, then uses the returned DPoP
session token for guard and reporting requests. It also reports a baseline set
of built-in OpenClaw tools so older OpenClaw versions without wrapped tool
metadata still expose a useful tool inventory to AgentGuard, including
top-level `input_params` for each catalog entry.

## Start OpenClaw

Generate a fresh user ticket in the AgentGuard console, then inject it into the
same shell where you start OpenClaw:

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

For long-running development sessions, run `openclaw gateway` under `tmux` or a
process manager, but still inject a fresh ticket before the process starts. Do
not reuse an old ticket after it has expired or has already been consumed.

## Test

```bash
node --test openclaw-adapter-js/agentguard-plugin/bridge.test.cjs
```
