# AgentGuard Plugins

`plugins` is the client-side local detection layer. It inspects normalized
`RuntimeEvent` objects before policy routing and returns a `CheckResult`.

Plugins do not execute tools, call LLMs, or make network requests. They only
read event data and return risk signals plus an optional decision candidate.

The active runtime event types are intentionally limited to:

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

## BasePlugin

All plugins should subclass `BasePlugin`:

```python
class BasePlugin:
    name: str = "base"
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError
```

### Fields

`name`

A readable plugin name. `PluginManager` uses it when recording plugin errors
in metadata, for example `tool_invoke_error`.

`event_types`

The event types this plugin handles. If empty, the plugin applies to all
events. In most cases, declare this explicitly so the plugin only runs in the
intended stage.

Example:

```python
event_types = [EventType.TOOL_INVOKE]
```

### Methods

`applies(event)`

Returns whether this plugin should process the event. The default behavior is:

- empty `event_types`: applies to all events
- `event.event_type in event_types`: applies to matching events

Usually you do not need to override this unless the plugin needs additional
payload or context filtering.

`check(event, context)`

The actual detection method. Subclasses must implement it. It receives a runtime
event and the current runtime context, and returns a `CheckResult`.

Client plugins currently receive only the current event. They do not receive
`trajectory_window`; trajectory context is sent to the remote server instead.

## check() Input

### event: RuntimeEvent

`RuntimeEvent` is AgentGuard's normalized runtime event object:

```python
RuntimeEvent(
    event_id: str,
    event_type: EventType,
    timestamp: float,
    context: RuntimeContext,
    payload: dict[str, Any],
    risk_signals: list[str],
    metadata: dict[str, Any],
)
```

Plugins usually read:

- `event.event_type`: the current event type
- `event.payload`: event content, with different shapes per stage
- `event.risk_signals`: signals already attached to the event
- `event.metadata`: additional runtime metadata

Common payload shapes:

```python
# llm_before / LLMInputPlugin
{"text": "..."}
{"messages": [{"role": "user", "content": "..."}]}

# llm_after / LLMOutputPlugin
{"output": output}

# tool_before / ToolInvokePlugin
{
    "tool_name": "send_email",
    "arguments": {"to": "...", "body": "..."},
    "capabilities": ["external_send"],
}

# tool_after / ToolResultPlugin
{
    "tool_name": "read_file",
    "result": "...",
    "error": None,
}
```

### context: RuntimeContext

`RuntimeContext` is the current session context:

```python
RuntimeContext(
    session_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    policy: str | None = None,
    policy_version: str | None = None,
    environment: str | None = None,
    metadata: dict[str, Any] = {},
)
```

Plugins can use it for user-, agent-, policy-, or environment-aware checks.

### trajectory_window

Client plugins do not receive `trajectory_window`. If a check needs recent
execution history, implement it as a server-side plugin.

When a client plugin returns a final local decision (`is_final=True`), the
client stores the `plugin_input`, `plugin_result`, event, context, and decision in
a local sync buffer. The next remote decision request sends those cached entries
as `client_cached_entries`; if a whole LLM/tool round finishes without needing a
remote decision, the runtime uploads the cached entries asynchronously for
server-side storage and audit.

## check() Output

`check()` must return a `CheckResult`:

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

### decision_candidate

An optional `GuardDecision` recommendation.

If the plugin only detects risk signals and does not want to decide, leave it
as `None`.

If the plugin finds a case that must be blocked, it can return:

```python
GuardDecision.deny(
    "Destructive shell command blocked by local plugin.",
    policy_id="local:dangerous_shell",
    risk_signals=["shell_command"],
)
```

### risk_signals

Risk labels detected by the plugin, for example:

```python
["prompt_injection", "secret_detected", "external_send"]
```

`PluginManager` merges all returned signals, deduplicates them, and writes them
back to `event.risk_signals`.

### is_final

Whether this plugin's `decision_candidate` should be treated as the final local
decision.

- `False`: this is only a candidate; the client sends the event to the remote server for the authoritative decision
- `True`: the plugin has made the final client-side decision; the remote server is skipped

Only deterministic high-risk checks should normally set `is_final=True`.

### metadata

Additional debug or detection information. `PluginManager` merges metadata from
all plugins into the final `CheckResult.metadata`.

## How PluginManager Calls Plugins

Plugins are configured and run by phase. No plugin is enabled by default when
`plugin_config` is omitted. A typical client config enables plugins like this:

```python
llm_before -> local ["llm_input"], remote []
llm_after -> local ["llm_output"], remote []
tool_before -> local ["tool_invoke"], remote []
tool_after -> local ["tool_result"], remote []
```

The client only loads the `local` list. The `remote` list is ignored by the
client and is intended for the server-side plugin manager.
The config must use the `{"phases": {...}}` shape. Each configured phase must
include both `local` and `remote`; legacy direct lists such as
`{"llm_before": ["llm_input"]}` are not accepted.

Event-to-phase mapping:

```python
LLM_INPUT -> llm_before
LLM_OUTPUT -> llm_after
TOOL_INVOKE -> tool_before
TOOL_RESULT -> tool_after
```

If multiple plugins are configured for the same phase, they run in order.

If a plugin raises an exception, `PluginManager` catches it, records the error
in metadata, and continues with the remaining plugins. A plugin should not
break the main runtime flow.

## Custom Plugin Example

```python
from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="block_private_tool",
    description="Block calls to private/internal tools.",
)
class BlockPrivateToolPlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        tool_name = event.payload.get("tool_name")
        if tool_name == "internal_admin":
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    "internal_admin is not allowed from this client.",
                    policy_id="local:block_private_tool",
                    risk_signals=["private_tool"],
                ),
                risk_signals=["private_tool"],
                is_final=True,
            )
        return CheckResult.empty()
```

Configuration example:

```json
{
  "phases": {
    "tool_before": {
      "local": [
        "tool_invoke",
        "block_private_tool"
      ],
      "remote": []
    }
  }
}
```

Pass the config when creating the client:

```python
guard = AgentGuard(
    session_id="s1",
    plugin_config="/path/to/plugins.json",
)
```

You can replace the plugin configuration while the client is running:

```python
guard.update_plugin_config({
    "phases": {
        "llm_before": {"local": ["llm_input"], "remote": []},
        "llm_after": {"local": [], "remote": []},
        "tool_before": {"local": ["tool_invoke"], "remote": []},
        "tool_after": {"local": ["tool_result"], "remote": []},
    }
})
```

The new configuration applies to the next guarded event. It does not re-run or
modify events that have already been checked.

The client can also expose a local HTTP endpoint for runtime updates:

```python
url = guard.start_config_api()
# default: http://127.0.0.1:38181/v1/client/plugins/config
```

List locally registered plugins:

```bash
curl http://127.0.0.1:38181/v1/client/plugins/list \
  -H 'X-AgentGuard-Session-Key: sk-...'
```

Response:

```json
{
  "status": "ok",
  "plugins": [
    {
      "name": "llm_input",
      "description": "Detect prompt-injection and system-prompt leak attempts in LLM input.",
      "event_types": ["llm_input"]
    }
  ]
}
```

Request:

```bash
curl -X POST http://127.0.0.1:38181/v1/client/plugins/config \
  -H 'Content-Type: application/json' \
  -H 'X-AgentGuard-Session-Key: sk-...' \
  -d '{"config":{"phases":{"llm_before":{"local":["llm_input"],"remote":[]},"llm_after":{"local":[],"remote":[]},"tool_before":{"local":["tool_invoke"],"remote":[]},"tool_after":{"local":["tool_result"],"remote":[]}}}}'
```

All client-local API endpoints require `X-AgentGuard-Session-Key`. The value is
the `session_key` on the `AgentGuard` instance; if none is provided explicitly,
the client generates a `sk-...` key automatically.

You can also pass a config file path:

```json
{"path": "/path/to/plugins.json"}
```

You can also upload new plugin code through the local API:

```bash
curl -X POST http://127.0.0.1:38181/v1/client/plugins/update \
  -H 'Content-Type: application/json' \
  -H 'X-AgentGuard-Session-Key: sk-...' \
  -d '{
    "event_type": "llm_input",
    "filename": "my_llm_input_plugin.py",
    "code": "from agentguard.plugins.base import BasePlugin, CheckResult\nfrom agentguard.plugins.registry import register\nfrom agentguard.schemas.events import EventType\n\n@register(name=\"my_llm_input\", description=\"My plugin.\")\nclass MyLLMInputPlugin(BasePlugin):\n    event_types = [EventType.LLM_INPUT]\n    def check(self, event, context):\n        return CheckResult(risk_signals=[\"my_signal\"])\n"
  }'
```

`event_type` determines where the code is written:

- `llm_input` -> `plugins/llm_before/`
- `llm_output` -> `plugins/llm_after/`
- `tool_invoke` -> `plugins/tool_before/`
- `tool_result` -> `plugins/tool_after/`

After writing the file, the client imports/reloads that module immediately so
`@register(...)` updates the runtime registry. The newly registered `name` can
then be used directly in plugin config.

## Adding a New Plugin

To add a plugin, put the plugin class in the matching phase folder and decorate
the class with `@register(name=..., description=...)`. The manager discovers plugin
modules under `agentguard.plugins`, runs the decorator, and then lets the config
refer to the plugin by `name`. With this mode, you do not need to modify
`__init__.py` or a built-in plugin map.

Each custom plugin must also define `event_types`. This tells the manager which
runtime event kinds the plugin applies to. Use `EventType.LLM_INPUT`,
`EventType.LLM_OUTPUT`, `EventType.TOOL_INVOKE`, or `EventType.TOOL_RESULT`.

Example file layout:

```text
agentguard/plugins/llm_before/my_plugin.py
```

Example plugin:

```python
from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="my_plugin",
    description="Short description of what this plugin detects.",
)
class MyPlugin(BasePlugin):
    event_types = [EventType.LLM_INPUT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        return CheckResult.empty()
```

Config:

```json
{
  "phases": {
    "llm_before": {
      "local": [
        "my_plugin"
      ],
      "remote": []
    }
  }
}
```

Then pass the config when creating the client:

```python
guard = AgentGuard(
    session_id="s1",
    plugin_config="/path/to/plugins.json",
)
```

The important part is the registered name: `my_plugin`. Plugin configs should
refer to registered names.
