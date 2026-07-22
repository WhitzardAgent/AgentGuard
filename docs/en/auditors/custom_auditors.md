# Custom Auditors

AgentGuard supports post-hoc auditing on the backend. Unlike plugins, which run inline during the live runtime, custom auditors run on the full stored trace for a `session_id` / `agent_id` / `user_id` tuple after events have already been recorded. This is useful for compliance review, incident triage, retrospective analysis, and generating summarized severity labels for the frontend.

The shared auditor abstractions live under:

```text
src/server/backend/audit/base.py
src/server/backend/audit/manager.py
src/server/backend/audit/registry.py
```

Concrete auditor implementations must be placed under:

```text
src/server/backend/audit/auditors/
```

The backend-discovered auditor interface is:

```python
from backend.audit.base import AuditResult, AuditTraceEntry, BaseAuditor
from backend.audit.registry import register


@register(
    name="my_trace_auditor",
    description="Summarize a stored trace into a severity label.",
)
class MyTraceAuditor(BaseAuditor):
    def audit(
        self,
        trace: list[AuditTraceEntry],
    ) -> AuditResult:
        if any((record.get("decision") or {}).get("decision_type") == "deny" for record in trace):
            return AuditResult(level="high", reason="The trace contains denied actions.")
        return AuditResult.ok()
```

Each `AuditTraceEntry` contains the canonical trace fields `session_id`, `agent_id`, `user_id`, `reason`, `event`, `decision`, `plugin_result`, `plugin_input`, `route`, and `timestamp`. Auditors should treat `event` as the primary runtime payload and the other fields as optional enrichments from the backend trace pipeline.

`AuditResult` currently uses four normalized severity levels: `critical`, `high`, `warning`, and `ok`. Each result also includes a human-readable `reason` and optional `metadata`.

The current type is defined in `src/server/backend/audit/base.py`:

```python
@dataclass
class AuditResult:
    level: AuditLevel = "ok"
    reason: str = "No issue detected in trace."
    metadata: dict[str, Any] = field(default_factory=dict)
```

### AuditResult fields

| Field | Type | Meaning | When to use it |
| --- | --- | --- | --- |
| `level` | `"critical" \| "high" \| "warning" \| "ok"` | The final severity produced by the auditor. Callers and UI usually read this first to understand how serious the trace is. | Set this whenever you want to express the overall risk of the trace. In practice, `ok` means no issue found, `warning` means suspicious signals worth reviewing, `high` means clear risk, and `critical` means urgent attention or escalation is needed. |
| `reason` | `str` | A human-readable summary explaining why the auditor chose this severity. | Almost always populate this. A full sentence works well because it can be shown directly in UI, logs, or API responses. |
| `metadata` | `dict[str, Any]` | Structured extra context for downstream consumers, dashboards, or follow-up automation. | Use this when the caller needs machine-readable details in addition to the headline conclusion, such as event IDs, risk signals, counts, tool names, or user identifiers. |

### Common AuditResult patterns

- Returning only the conclusion:

  ```python
  return AuditResult(level="high", reason="The trace contains denied high-risk actions.")
  ```

- Returning conclusion plus structured details:

  ```python
  return AuditResult(
      level="warning",
      reason="Suspicious outbound behavior detected.",
      metadata={
          "event_ids": suspicious_event_ids,
          "risk_signals": sorted(risky_signals),
          "tool_names": sorted(tool_names),
      },
  )
  ```

- Returning a clean result when no issue is found:

  ```python
  return AuditResult.ok()
  ```

### AuditResult helper methods

| Member | What it does | When to use it |
| --- | --- | --- |
| `AuditResult.ok(reason="No issue detected in trace.")` | Quickly builds a result with `level="ok"`. | Use this when the auditor found no issue and you want the shortest, most explicit success return path. |
| `result.to_dict()` | Converts the result into a serializable dictionary with `level`, `reason`, and `metadata`. | Use this when returning data through API layers, writing snapshot tests, logging, or doing extra JSON serialization. |

## AuditTraceEntry

`AuditTraceEntry` is the normalized record type passed into `BaseAuditor.audit()`. One entry usually represents one stored runtime event plus the decision and detection metadata produced for that event.

The current type is defined in `src/server/backend/audit/base.py`:

```python
@dataclass
class AuditTraceEntry:
    session_id: str
    agent_id: str | None = None
    user_id: str | None = None
    reason: str | None = None
    event: RuntimeEvent | None = None
    decision: GuardDecision | None = None
    plugin_result: dict[str, Any] = field(default_factory=dict)
    plugin_input: dict[str, Any] = field(default_factory=dict)
    route: str | None = None
    timestamp: float | None = None
```

### Fields

| Field | Type | Meaning | How to use it |
| --- | --- | --- | --- |
| `session_id` | `str` | The session/run identifier this trace entry belongs to. | Group or verify entries that should belong to the same run. |
| `agent_id` | `str or None` | The agent identity associated with the event, if available. | Scope auditor findings to one agent or include it in metadata. |
| `user_id` | `str or None` | The end-user identity associated with the event, if available. | Detect user-specific risk patterns or include user context in reports. |
| `reason` | `str or None` | Why the record was stored, such as `guard_decide`, `round_complete`, or `client_error`. | Distinguish normal remote decisions from uploaded local cache entries or error-path syncs. |
| `event` | `RuntimeEvent or None` | The normalized runtime event: LLM input, LLM output, tool invocation, or tool result. | This is usually the main payload to inspect: event type, tool name, arguments, result, risk signals, and metadata. |
| `decision` | `GuardDecision or None` | The decision returned for the event, if one exists. | Count denies/reviews, read the decision reason, or identify whether a risky action was blocked. |
| `plugin_result` | `dict[str, Any]` | Merged runtime detection output for the event. This is where plugin risk metadata is stored. | Read `risk_signals`, detection metadata, or plugin-produced context that was attached during runtime. |
| `plugin_input` | `dict[str, Any]` | The input payload passed into the plugin pipeline when available. | Inspect the raw event/context payload that led to a plugin result. |
| `route` | `str or None` | The runtime path that produced the trace entry, if recorded. | Distinguish remote decisions, local sync uploads, and other runtime routes. |
| `timestamp` | `float or None` | Trace entry timestamp, if recorded. | Order records or compute time windows during audit. |

### Helper methods and properties

| Member | What it does | When to use it |
| --- | --- | --- |
| `AuditTraceEntry.from_dict(data)` | Builds a normalized entry from a raw trace dictionary. It extracts `event`, `decision`, identity fields, `reason`, `plugin_result`, `plugin_input`, `route`, and `timestamp` when present. | Use this when an auditor or test receives raw stored trace dictionaries instead of `AuditTraceEntry` objects. |
| `entry.to_dict()` | Converts the entry back into a serializable dictionary. It includes `event.to_dict()` and `decision.to_dict()` when those objects exist. | Use this for debugging, logging, test snapshots, or returning normalized trace details. |
| `entry.merged_with(incoming)` | Returns a new entry by merging another entry into the current one. Incoming identity, event, decision, reason, route, and timestamp take precedence when present; `plugin_result` and `plugin_input` dictionaries are merged. | Useful when server-side and client-uploaded records describe the same event and need to be consolidated. |
| `entry.event_id` | Convenience property returning `entry.event.event_id`, or `None` if there is no event. | Use this to deduplicate events or include event IDs in audit metadata. |

### `event`, `decision`, and `plugin_result`

These three fields are the main inputs most auditors read:

- `event: RuntimeEvent | None = None`

  `event` is the original runtime event being audited. It tells you what happened: the event type, typed payload, context, risk signals, and adapter metadata. For example, a `TOOL_INVOKE` event exposes `event.payload.tool_name`, `event.payload.arguments`, and `event.payload.capabilities`; an `LLM_INPUT` event exposes `event.payload.messages`; an `LLM_OUTPUT` event exposes `event.payload.output`, `event.payload.thought`, and `event.payload.final_output`.

  Use `event` when the auditor needs to inspect the actual runtime behavior:

  ```python
  if entry.event and entry.event.event_type.value == "tool_invoke":
      tool_name = entry.event.payload.tool_name
      arguments = entry.event.payload.arguments
  ```

  It can be `None` if the stored trace record did not contain a parseable runtime event, so auditors should always check it before reading event fields.

- `decision: GuardDecision | None = None`

  `decision` is the decision AgentGuard produced for the event. It tells you how the runtime handled the event: allow, deny, review, degrade, sanitize, and so on. It also carries the decision reason, policy ID, risk signals, and metadata when available.

  Use `decision` when the auditor needs to summarize enforcement outcomes:

  ```python
  if entry.decision and entry.decision.decision_type.value == "deny":
      denied_event_ids.append(entry.event_id)
      reasons.append(entry.decision.reason)
  ```

  It can be `None` for trace entries that were uploaded without a final decision or entries that only carry partial runtime context.

- `plugin_result: dict[str, Any] = field(default_factory=dict)`

  `plugin_result` stores the merged detection result produced during runtime. Typical contents include `risk_signals`, `metadata`, `is_final`, or decision-candidate details depending on the runtime path.

  Use `plugin_result` when the auditor wants the detection details that may not be visible from the final decision alone:

  ```python
  signals = entry.plugin_result.get("risk_signals") or []
  metadata = entry.plugin_result.get("metadata") or {}
  ```

  Unlike `event` and `decision`, this field is always a dictionary; it is empty when no plugin metadata was stored.

- `plugin_input: dict[str, Any] = field(default_factory=dict)`

  `plugin_input` stores the input passed to the plugin pipeline when the trace source recorded it. Use it when an auditor needs to compare what the plugin saw with the normalized `event` or final `decision`.

### Common usage patterns

Most auditors start by iterating through the full trace and collecting signals, decisions, tool calls, or identities:

```python
def audit(self, trace: list[AuditTraceEntry]) -> AuditResult:
    denied_events = []
    risky_signals = set()

    for entry in trace:
        if entry.decision and entry.decision.decision_type.value == "deny":
            denied_events.append(entry.event_id)

        if entry.event:
            risky_signals.update(entry.event.risk_signals)
            if entry.event.event_type.value == "tool_invoke" and entry.event.payload.tool_name == "send_email":
                recipient = entry.event.payload.arguments.get("addr")
                if recipient and not recipient.endswith("@example.com"):
                    risky_signals.add("external_email")

        risky_signals.update(entry.plugin_result.get("risk_signals") or [])

    if denied_events or risky_signals:
        return AuditResult(
            level="high",
            reason="Trace contains risky signals or denied events.",
            metadata={
                "denied_events": denied_events,
                "risk_signals": sorted(risky_signals),
            },
        )
    return AuditResult.ok()
```

When writing an auditor, treat `event`, `decision`, `agent_id`, and `user_id` as optional. Stored traces can come from different runtime paths, so defensive `None` checks make the auditor robust.

After you add the auditor implementation, the backend discovers it by registered name. The frontend can then:

- call `GET /v1/backend/auditors` to list available auditors and descriptions
- call `POST /v1/backend/audit/custom/run` with `session_id`, `agent_id`, `user_id`, and `auditor_name` to run one auditor on the corresponding stored trace

For a concrete built-in example, see `src/server/backend/audit/auditors/trace_risk_summary.py`.
