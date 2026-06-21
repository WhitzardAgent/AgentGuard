# Custom Server Plugins

Server plugins run on the AgentGuard control server. They are useful for centralized policy decisions, cross-step detection, and checks that need recent session history.

Server plugin files should be placed under the phase folder that matches the event type:

```text
src/server/backend/runtime/plugins/llm_before/
src/server/backend/runtime/plugins/llm_after/
src/server/backend/runtime/plugins/tool_before/
src/server/backend/runtime/plugins/tool_after/
```

## Input

A server plugin implements this method:

```python
def check(
    self,
    event: RuntimeEvent,
    context: RuntimeContext,
    trajectory_window: list[RuntimeEvent] | None = None,
) -> CheckResult:
    ...
```

The server plugin manager calls `check()` only when the current event phase matches the configured phase and `event_types` allows the event.

### `event: RuntimeEvent`

`event` is the normalized runtime event that the plugin should inspect:

```python
RuntimeEvent(
    event_id: str,
    event_type: EventType,
    timestamp: float,
    context: RuntimeContext,
    payload: LLMInput | LLMOutput | ToolInvoke | ToolResult,
    risk_signals: list[str] = [],
    metadata: dict[str, Any] = {},
)
```

- `event_id`: unique identifier for the event.
- `event_type`: current event type. Supported values are `LLM_INPUT`, `LLM_OUTPUT`, `TOOL_INVOKE`, and `TOOL_RESULT`.
- `timestamp`: event creation time.
- `context`: the same runtime context passed as the second argument.
- `payload`: one of the four typed payload classes: `LLMInput`, `LLMOutput`, `ToolInvoke`, or `ToolResult`.
- `risk_signals`: risk labels already attached by client plugins, preprocessors, or earlier server plugins.
- `metadata`: adapter-specific or debug metadata.

Common payload shapes:

```python
# LLM_INPUT
LLMInput(messages=[{"role": "user", "content": "..."}])

# LLM_OUTPUT
LLMOutput(output="...")

# TOOL_INVOKE
ToolInvoke(
    tool_name="send_email",
    arguments={"to": "...", "body": "..."},
    capabilities=["external_send"],
)

# TOOL_RESULT
ToolResult(tool_name="read_file", result="...")
```

### `context: RuntimeContext`

`context` describes the current session and agent identity:

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

- `session_id`: required session identifier.
- `user_id`: optional end-user identity.
- `agent_id`: optional agent instance or service identity.
- `task_id`: optional workflow or task identifier.
- `policy`: optional policy name, source, or mode.
- `policy_version`: optional policy version or snapshot identifier.
- `environment`: optional runtime environment such as `dev`, `staging`, or `prod`.
- `metadata`: free-form extra context.

### `trajectory_window: list[RuntimeEvent] | None`

`trajectory_window` is only available to server plugins.

- It contains recent events from the same session.
- Each item is a full `RuntimeEvent`.
- It can include client-side cached plugin decisions that were synchronized to the server.
- Use it for multi-step checks, such as detecting sensitive data read in one tool result and then sent through a later outbound tool call.

Always handle `None` defensively:

```python
trajectory_window = trajectory_window or []
```

### Configuration Input

Server plugin specs are read from the `server` list in `config/plugins.json` or the runtime plugin config:

```json
{
  "phases": {
    "tool_before": {
      "client": [],
      "server": [
        {
          "name": "my_server_plugin",
          "env": {}
        }
      ]
    }
  }
}
```

- `name`: registered plugin name.
- `class` or `plugin`: optional import-path alternatives to `name`.
- The current server runtime resolves plugin classes by `name` or import path.
- Extra fields may remain in stored config, but the current server plugin manager does not inject `env` or `kwargs` into server plugin constructors.

## Output

`check()` must return `CheckResult`:

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

- `decision_candidate`: optional `GuardDecision` recommendation. Use it when the plugin wants to propose `ALLOW`, `DENY`, `SANITIZE`, `HUMAN_CHECK`, `LLM_CHECK`, or another supported decision.
- `risk_signals`: risk labels detected by this plugin. The manager deduplicates them and writes them back to `event.risk_signals`.
- `is_final`: whether `decision_candidate` should be treated as the authoritative server-side decision. If `True`, the runtime can use this decision directly.
- `metadata`: structured debug or detection details. The manager merges plugin metadata into the final plugin result.

Return `CheckResult.empty()` when the plugin has no finding.

## Example

```python
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import GuardDecision
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
        tool_name = event.payload.tool_name

        saw_sensitive_read = any(
            item.event_type == EventType.TOOL_RESULT
            and "secret_detected" in (item.risk_signals or [])
            for item in trajectory_window
        )

        if saw_sensitive_read and tool_name == "send_email":
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    "Sensitive data cannot be sent by email.",
                    policy_id="server:block_exfiltration",
                    risk_signals=["cross_step_exfiltration"],
                ),
                risk_signals=["cross_step_exfiltration"],
                is_final=True,
                metadata={"trajectory_events": len(trajectory_window)},
            )

        return CheckResult.empty()
```

Configuration:

```json
{
  "phases": {
    "tool_before": {
      "client": [],
      "server": ["my_server_plugin"]
    }
  }
}
```
