# AgentGuard Plugins

AgentGuard supports plugins on both the client and the server. Both sides use the same normalized runtime schema, but they do not see the same input scope and they are not deployed to the same location. For implementation-level details, see `../../src/client/python/agentguard/plugins/README.md` and `../../src/server/backend/runtime/plugins/`.

## Client vs. Server Plugins

- **Client plugins** run locally inside the agent process. They receive only the current `event: RuntimeEvent` and `context: RuntimeContext`, so they are best for lightweight low-latency filtering before a remote decision.
- **Server plugins** run on the control server. They receive the current `event`, the current `context`, and `trajectory_window: list[RuntimeEvent]`, so they are best for cross-step detection, centralized policy evaluation, and auditing.
- Client plugin files must be placed under `../../src/client/python/agentguard/plugins/<phase>/`.
- Server plugin files must be placed under `../../src/server/backend/runtime/plugins/`.

## Built-in `rule_based_plugin` Plugin

AgentGuard includes a built-in server plugin named `rule_based_plugin`. It is designed for rule-configured tool-call protection: users write or generate DSL policies, and the plugin evaluates those rules against the current tool invocation and recent session trajectory. When a rule matches, it can identify the security risk and return a decision such as `DENY`, `HUMAN_CHECK`, or `LLM_CHECK` before the tool call executes.

In the default quick-start flow, `rule_based_plugin` is configured as a remote plugin in the `tool_before` phase:

```json
{
  "phases": {
    "tool_before": {
      "local": [],
      "remote": [{"name": "rule_based_plugin", "env": {}}]
    }
  }
}
```

Use this plugin when you want explicit, auditable rules for cases such as blocking shell commands, preventing non-allowlisted outbound requests, or stopping sensitive data from flowing into email, HTTP, or messaging tools.

## RuntimeEvent

`RuntimeEvent` is the normalized event object shared by client and server plugins:

```python
RuntimeEvent(
    event_id: str,
    event_type: EventType,
    timestamp: float,
    context: RuntimeContext,
    payload: dict[str, Any],
    risk_signals: list[str] = [],
    metadata: dict[str, Any] = {},
)
```

- `event_id`: unique identifier for the current runtime event.
- `event_type`: current runtime stage. Active values are `LLM_INPUT`, `LLM_OUTPUT`, `TOOL_INVOKE`, and `TOOL_RESULT`.
- `timestamp`: event creation time.
- `context`: the shared runtime context attached to this event.
- `payload`: the stage-specific content the plugin actually inspects.
- `risk_signals`: risk labels already attached by earlier plugins.
- `metadata`: extra debug or adapter-specific information carried with the event.

Common payload shapes:

```python
# LLM_INPUT
{"messages": [...]}
{"text": "..."}  # compatibility/simple adapters

# LLM_OUTPUT
{"output": ...}

# TOOL_INVOKE
{
    "tool_name": "send_email",
    "arguments": {"to": "...", "body": "..."},
    "capabilities": ["external_send"],
}

# TOOL_RESULT
{
    "tool_name": "read_file",
    "result": ...,
    "error": None,
}
```

## RuntimeContext

`RuntimeContext` is the session-level context propagated across events:

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

- `session_id`: required session identifier used to associate all events in the same run.
- `user_id`: optional end-user identity behind the agent request.
- `agent_id`: optional agent instance or service identity.
- `task_id`: optional task or workflow identifier for the current unit of work.
- `policy`: optional logical policy name, source, or mode attached to the session.
- `policy_version`: optional policy version or snapshot identifier.
- `environment`: optional runtime environment such as `dev`, `staging`, or `prod`.
- `metadata`: free-form additional context such as tenant info, framework labels, or adapter-specific fields.

## `trajectory_window: list[RuntimeEvent]`

`trajectory_window` is only available to server-side plugins.

- It is a recent event window for the same session.
- Each element in the list is a full `RuntimeEvent`.
- Use it when detection depends on execution history instead of only the current event.
- Typical cases include "tool result exposed sensitive data, then a later tool call tries to send it externally" or "untrusted LLM output later flows into a shell command."

Client plugins do not receive `trajectory_window`. If your detection logic requires history, implement it as a server-side plugin. In practice, the server window can include both the normal runtime trace and cached local decisions synchronized from the client.

## Custom Plugin

### Client-side plugin

Client plugins must be placed in the phase folder that matches the event type:

```text
../../src/client/python/agentguard/plugins/llm_before/
../../src/client/python/agentguard/plugins/llm_after/
../../src/client/python/agentguard/plugins/tool_before/
../../src/client/python/agentguard/plugins/tool_after/
```

Example:

```python
from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="my_client_plugin",
    description="Detect risky tool arguments on the client side.",
)
class MyClientPlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        tool_name = event.payload.get("tool_name")
        arguments = event.payload.get("arguments") or {}
        if tool_name == "send_email" and arguments.get("to", "").endswith("@external.com"):
            return CheckResult(risk_signals=["external_send"])
        return CheckResult.empty()
```

### Server-side plugin

Server plugins must be placed under the server plugin directory:

```text
../../src/server/backend/runtime/plugins/
```

Example:

```python
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="my_server_plugin",
    description="Detect multi-step exfiltration on the server side.",
)
class MyServerPlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        trajectory_window = trajectory_window or []
        if trajectory_window and event.payload.get("tool_name") == "send_email":
            return CheckResult(risk_signals=["cross_step_review"])
        return CheckResult.empty()
```

The server-side plugin directory is `../../src/server/backend/runtime/plugins/`.

### Plugin configuration

After adding the plugin classes, reference them with plugin spec objects in plugin config. The `name` field is the registered plugin name. For client-side `local` plugins, `env`, `kwargs`, and top-level constructor keys are supported and passed into the plugin instance. For server-side `remote` plugins, the current runtime resolves the plugin by `name` or `class` and does not inject `env`/`kwargs` into the constructor.

```json
{
  "phases": {
    "tool_before": {
      "local": [
        {
          "name": "my_client_plugin",
          "env": {}
        }
      ],
      "remote": [
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

- `local` is loaded by the client plugin manager.
- `remote` is loaded by the server plugin manager.
- `local` plugin specs can use `name`, optional `env`, and optional constructor settings through `kwargs` or top-level keys.
- `remote` plugin specs currently use `name` (or `class`) for resolution; extra fields may remain in config storage but are not injected into server plugin constructors.
- Even if both plugin specs appear in the same config file, the implementation files must still be deployed to the correct client or server folder.
