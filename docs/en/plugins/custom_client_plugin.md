# Custom Client Plugins

Client plugins run inside the agent process. They are useful for low-latency checks that only need the current event, such as detecting risky tool arguments before a tool call leaves the client.

Client plugin files must be placed under the phase folder that matches the event type:

```text
src/client/python/agentguard/plugins/llm_before/
src/client/python/agentguard/plugins/llm_after/
src/client/python/agentguard/plugins/tool_before/
src/client/python/agentguard/plugins/tool_after/
```

## Input

A client plugin implements this method:

```python
def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
    ...
```

The client plugin manager calls `check()` only when the current event phase matches the configured phase and `event_types` allows the event.

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
- `risk_signals`: risk labels already attached by earlier plugins.
- `metadata`: adapter-specific or debug metadata.

Common payload shapes:

```python
# LLM_INPUT
LLMInput(messages=[{"role": "user", "content": "..."}])

# LLM_OUTPUT
LLMOutput(output="...", thought=None, final_output=None)

# TOOL_INVOKE
ToolInvoke(
    tool_name="send_email",
    arguments={"to": "...", "body": "..."},
    capabilities=["external_send"],
)

# TOOL_RESULT
ToolResult(tool_name="read_file", result="...")
```

For `LLMOutput`, the three fields have different purposes:

- `payload.output`: canonical text for backward compatibility and general-purpose policy checks.
- `payload.thought`: optional hidden reasoning text when the adapter can separate it.
- `payload.final_output`: optional user-visible final answer.

In most plugins, start with `payload.output`. Only read `payload.thought` or `payload.final_output` when your logic explicitly cares about that distinction.

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

Client plugins do not receive `trajectory_window`. If a check needs previous events from the same session, implement it as a server plugin.

### Configuration Input

Client plugin specs are read from the `client` list in `config/plugins.json` or the runtime plugin config:

```json
{
  "phases": {
    "tool_before": {
      "client": [
        {
          "name": "my_client_plugin",
          "env": {
            "API_KEY": "$MY_PLUGIN_API_KEY"
          },
          "kwargs": {
            "blocked_domain": "external.com"
          }
        }
      ],
      "server": []
    }
  }
}
```

- `name`: registered plugin name.
- `env`: optional environment mapping. Values like `$MY_PLUGIN_API_KEY` are resolved from process environment variables.
- `kwargs`: optional constructor settings.
- Extra top-level keys outside `name`, `env`, and `kwargs` are also passed as constructor settings.

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

- `decision_candidate`: optional `GuardDecision` recommendation. Use it when the plugin wants to propose `ALLOW` (allow immediately), `DENY` (block execution), `SANITIZE` (modify the input content and continue execution), `HUMAN_CHECK` (keep the action pending until a user decides on the server whether to allow it), `LOG_ONLY` (annotate it in audit logs but still allow it), or another supported decision.
- `risk_signals`: risk labels detected by this plugin. The manager deduplicates them and writes them back to `event.risk_signals`.
- `is_final`: whether `decision_candidate` should be treated as the final client-side decision. If `True`, the client can skip the server decision path for this event. Use this only for deterministic high-confidence checks.
- `metadata`: structured debug or detection details. The manager merges plugin metadata into the final plugin result.

Return `CheckResult.empty()` when the plugin has no finding.

## Example

```python
from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="my_client_plugin",
    description="Detect risky email destinations before tool execution.",
)
class MyClientPlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        tool_name = event.payload.tool_name
        arguments = event.payload.arguments
        recipient = str(arguments.get("to") or "")

        if tool_name == "send_email" and recipient.endswith("@external.com"):
            return CheckResult(
                decision_candidate=GuardDecision.deny(
                    "External email destination blocked by client plugin.",
                    policy_id="client:block_external_email",
                    risk_signals=["external_send"],
                ),
                risk_signals=["external_send"],
                is_final=True,
                metadata={"recipient": recipient},
            )

        return CheckResult.empty()
```

Configuration:

```json
{
  "phases": {
    "tool_before": {
      "client": ["my_client_plugin"],
      "server": []
    }
  }
}
```
