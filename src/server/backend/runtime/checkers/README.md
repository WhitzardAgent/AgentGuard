# Server Runtime Checkers

`backend.runtime.checkers` is the server-side checker layer. It runs when the
server receives a `/v1/guard/decide` request and inspects the request's
`current_event` before plugins and policy evaluation.

Server checkers use the same event model as the client. The active runtime event
types are:

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

## BaseChecker

All server checkers subclass `BaseChecker`:

```python
class BaseChecker:
    name: str = "base"
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError
```

`check(event, context, trajectory_window=None)` receives:

- `event`: the normalized `RuntimeEvent` created from `current_event`
- `context`: the request/session `RuntimeContext`
- `trajectory_window`: the recent event window sent by the client request

It returns `CheckResult`:

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

`CheckerManager` merges risk signals, attaches them to the event, and includes
the merged checker result in the server response as `checker_result`.

Unlike client checkers, server checkers can inspect `trajectory_window`. Use it
for trajectory-level checks such as "tool_result contained a secret, then the
current tool_invoke tries to send externally."

`trajectory_window` is built from both the request's normal `trajectory_window`
and any `client_cached_entries` sent by the client. Those cached entries are
local checker decisions from earlier events that skipped the server. The server
also stores uploaded cached entries from `/v1/trace/upload` for audit.

## Configured Phases

No checker is enabled by default when `checker_config` is omitted. A typical
server config enables remote checkers like this:

```python
llm_before -> local [], remote ["llm_input"]
llm_after -> local [], remote ["llm_output"]
tool_before -> local [], remote ["tool_invoke"]
tool_after -> local [], remote ["tool_result"]
```

The server only loads the `remote` list. The `local` list is ignored by the
server and is intended for client-side checker execution.
The config must use the `{"phases": {...}}` shape. Each configured phase must
include both `local` and `remote`; legacy direct lists such as
`{"tool_before": ["tool_invoke"]}` are not accepted.

Event-to-phase mapping:

```python
LLM_INPUT -> llm_before
LLM_OUTPUT -> llm_after
TOOL_INVOKE -> tool_before
TOOL_RESULT -> tool_after
```

If multiple checkers are configured for the same phase, they run in order.

## Adding a New Checker

Put the checker class in the matching phase folder and reference it by full
import path in the checker config. With this mode, you do not need to modify
`__init__.py` or `_BUILTIN_CHECKERS`.

The server rule matcher is also implemented as a checker at:

```text
backend/runtime/checkers/tool_before/rule_based_check/checker.py
```

It is available as `rule_based_check` or by full import path:
`backend.runtime.checkers.tool_before.rule_based_check.RuleBasedChecker`.
It is optional: include it in the checker config when you want server-side
rule-based decisions. When enabled through `RuntimeManager`, it is bound to the
same live policy store used by the console.

Example file layout:

```text
backend/runtime/checkers/tool_before/my_checker.py
```

Example checker:

```python
from backend.runtime.checkers.base import BaseChecker, CheckResult
from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent


class MyServerChecker(BaseChecker):
    name = "my_server_checker"
    event_types = [EventType.TOOL_INVOKE]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        return CheckResult.empty()
```

Config file:

```json
{
  "phases": {
    "tool_before": {
      "local": [],
      "remote": [
        "tool_invoke",
        "backend.runtime.checkers.tool_before.my_checker.MyServerChecker"
      ]
    }
  }
}
```

The important part is the full path:
`backend.runtime.checkers.tool_before.my_checker.MyServerChecker`. Because the
config points directly to the module and class, the manager can import it
without package re-export or built-in short-name registration.

## Loading the Config

When constructing the server manager directly:

```python
from backend.runtime.manager import RuntimeManager

manager = RuntimeManager(checker_config="/path/to/server_checkers.json")
```

When running the FastAPI server, set one of these environment variables:

```bash
export AGENTGUARD_SERVER_CHECKER_CONFIG=/path/to/server_checkers.json
```

or:

```bash
export AGENTGUARD_CHECKER_CONFIG=/path/to/server_checkers.json
```

`AGENTGUARD_SERVER_CHECKER_CONFIG` has priority over `AGENTGUARD_CHECKER_CONFIG`.

You can also update checker configuration at runtime through the backend API:

```bash
curl -X POST http://127.0.0.1:8000/v1/checkers/config \
  -H 'Content-Type: application/json' \
  -d '{
    "config": {
      "phases": {
        "tool_before": {
          "local": [],
          "remote": ["tool_invoke", "rule_based_check"]
        }
      }
    },
    "client_config_urls": [
      "http://127.0.0.1:38181/v1/client/checkers/config"
    ]
  }'
```

The backend updates its own server checker manager first. If `client_config_urls`
is provided, it forwards `{"config": ...}` to each client URL and returns the
per-client result in `client_updates`. Use `client_config` when the client should
receive a different config from the server:

```json
{
  "config": {
    "phases": {
      "tool_before": {"local": [], "remote": ["rule_based_check"]}
    }
  },
  "client_config": {
    "phases": {
      "tool_after": {"local": ["tool_result"], "remote": []}
    }
  },
  "client_config_urls": ["http://127.0.0.1:38181/v1/client/checkers/config"]
}
```
