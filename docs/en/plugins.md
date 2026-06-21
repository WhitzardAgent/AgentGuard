# AgentGuard Plugins

AgentGuard supports plugins on both the client and the server. Both sides use the same normalized runtime schema, but they do not see the same input scope and they are not deployed to the same location. For implementation-level details, see `src/client/python/agentguard/plugins/README.md` and `src/server/backend/runtime/plugins/`.

## Client vs. Server Plugins

- **Client plugins** run locally inside the agent process. They receive only the current `event: RuntimeEvent` and `context: RuntimeContext`, so they are best for lightweight low-latency filtering before a remote decision.
- **Server plugins** run on the control server. They receive the current `event`, the current `context`, and `trajectory_window: list[RuntimeEvent]`, so they are best for cross-step detection, centralized policy evaluation, and auditing.
- Client plugin files must be placed under `src/client/python/agentguard/plugins/<phase>/`.
- Server plugin files must be placed under `src/server/backend/runtime/plugins/<phase>/`.


## Plugin Configuration

After adding the plugin classes, reference them with plugin spec objects in plugin config. The `name` field is the registered plugin name. Client plugin specs support `env`, `kwargs`, and top-level constructor keys, which are passed into the plugin instance. Server plugin specs are resolved by `name` or `class`; the current server runtime does not inject `env` or `kwargs` into server plugin constructors.

```json
{
  "phases": {
    "tool_before": {
      "client": [
        {
          "name": "my_client_plugin",
          "env": {}
        }
      ],
      "server": [
        {
          "name": "rule_based_plugin",
          "env": {}
        },
        {
          "name": "my_server_plugin",
          "env": {}
        }
      ]
    }
  }
}
```

- `client` is loaded by the client plugin manager.
- `server` is loaded by the server plugin manager.
- `client` plugin specs can use `name`, optional `env`, and optional constructor settings through `kwargs` or top-level keys.
- `server` plugin specs currently use `name` (or `class`) for resolution; extra fields may remain in config storage but are not injected into server plugin constructors.
- Even if both plugin specs appear in the same config file, the implementation files must still be deployed to the correct client or server folder.

## Plugin Categories

Plugin implementation details live in separate pages under this section:

- [Builtin Plugins](plugins/builtin_plugins.md)
- [Custom Client Plugins](plugins/custom_client_plugin.md)
- [Custom Server Plugins](plugins/custom_server_plugin.md)
