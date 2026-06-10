# AgentGuard Checkers

`checkers` is the client-side local detection layer. It inspects normalized
`RuntimeEvent` objects before policy routing and returns a `CheckResult`.

Checkers do not execute tools, call LLMs, or make network requests. They only
read event data and return risk signals plus an optional decision candidate.

The active runtime event types are intentionally limited to:

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

## BaseChecker

All checkers should subclass `BaseChecker`:

```python
class BaseChecker:
    name: str = "base"
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError
```

### Fields

`name`

A readable checker name. `CheckerManager` uses it when recording checker errors
in metadata, for example `tool_invoke_error`.

`event_types`

The event types this checker handles. If empty, the checker applies to all
events. In most cases, declare this explicitly so the checker only runs in the
intended stage.

Example:

```python
event_types = [EventType.TOOL_INVOKE]
```

### Methods

`applies(event)`

Returns whether this checker should process the event. The default behavior is:

- empty `event_types`: applies to all events
- `event.event_type in event_types`: applies to matching events

Usually you do not need to override this unless the checker needs additional
payload or context filtering.

`check(event, context)`

The actual detection method. Subclasses must implement it. It receives a runtime
event and the current runtime context, and returns a `CheckResult`.

Client checkers currently receive only the current event. They do not receive
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

Checkers usually read:

- `event.event_type`: the current event type
- `event.payload`: event content, with different shapes per stage
- `event.risk_signals`: signals already attached to the event
- `event.metadata`: additional runtime metadata

Common payload shapes:

```python
# llm_before / LLMInputChecker
{"text": "..."}
{"messages": [{"role": "user", "content": "..."}]}

# llm_after / LLMOutputChecker
{"output": output}

# tool_before / ToolInvokeChecker
{
    "tool_name": "send_email",
    "arguments": {"to": "...", "body": "..."},
    "capabilities": ["external_send"],
}

# tool_after / ToolResultChecker
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

Checkers can use it for user-, agent-, policy-, or environment-aware checks.

### trajectory_window

Client checkers do not receive `trajectory_window`. If a check needs recent
execution history, implement it as a server-side checker or server plugin.

When a client checker returns a final local decision (`is_final=True`), the
client stores the checker input, checker result, event, context, and decision in
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

If the checker only detects risk signals and does not want to decide, leave it
as `None`.

If the checker finds a case that must be blocked, it can return:

```python
GuardDecision.deny(
    "Destructive shell command blocked by local checker.",
    policy_id="local:dangerous_shell",
    risk_signals=["shell_command"],
)
```

### risk_signals

Risk labels detected by the checker, for example:

```python
["prompt_injection", "secret_detected", "external_send"]
```

`CheckerManager` merges all returned signals, deduplicates them, and writes them
back to `event.risk_signals`.

### is_final

Whether this checker's `decision_candidate` should be treated as the final local
decision.

- `False`: this is only a candidate; the client sends the event to the remote server for the authoritative decision
- `True`: the checker has made the final client-side decision; the remote server is skipped

Only deterministic high-risk checks should normally set `is_final=True`.

### metadata

Additional debug or detection information. `CheckerManager` merges metadata from
all checkers into the final `CheckResult.metadata`.

## How CheckerManager Calls Checkers

Checkers are configured and run by phase. No checker is enabled by default when
`checker_config` is omitted. A typical client config enables checkers like this:

```python
llm_before -> local ["llm_input"], remote []
llm_after -> local ["llm_output"], remote []
tool_before -> local ["tool_invoke"], remote []
tool_after -> local ["tool_result"], remote []
```

The client only loads the `local` list. The `remote` list is ignored by the
client and is intended for the server-side checker manager.
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

If multiple checkers are configured for the same phase, they run in order.

If a checker raises an exception, `CheckerManager` catches it, records the error
in metadata, and continues with the remaining checkers. A checker should not
break the main runtime flow.

## Custom Checker Example

```python
from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


class BlockPrivateToolChecker(BaseChecker):
    name = "block_private_tool"
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
        "my_package.checkers.BlockPrivateToolChecker"
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
    checker_config="/path/to/checkers.json",
)
```

You can replace the checker configuration while the client is running:

```python
guard.update_checker_config({
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
# default: http://127.0.0.1:38181/v1/client/checkers/config
```

Request:

```bash
curl -X POST http://127.0.0.1:38181/v1/client/checkers/config \
  -H 'Content-Type: application/json' \
  -d '{"config":{"phases":{"llm_before":{"local":["llm_input"],"remote":[]},"llm_after":{"local":[],"remote":[]},"tool_before":{"local":["tool_invoke"],"remote":[]},"tool_after":{"local":["tool_result"],"remote":[]}}}}'
```

You can also pass a config file path:

```json
{"path": "/path/to/checkers.json"}
```

## Adding a New Checker

To add a checker, put the checker class in the matching phase folder and refer to
it by full import path in the checker config. With this mode, you do not need to
modify `__init__.py` or `_BUILTIN_CHECKERS`.

Example file layout:

```text
agentguard/checkers/llm_before/my_checker.py
```

Example checker:

```python
from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


class MyChecker(BaseChecker):
    name = "my_checker"
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
        "agentguard.checkers.llm_before.my_checker.MyChecker"
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
    checker_config="/path/to/checkers.json",
)
```

The important part is the full path:
`agentguard.checkers.llm_before.my_checker.MyChecker`. Because the config points
directly to the module and class, the manager can import it without any package
re-export or built-in short-name registration.
