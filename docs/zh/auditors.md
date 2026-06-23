# 自定义审计器

AgentGuard 支持在后端执行事后审计。与在运行时链路中同步执行的 plugin 不同，自定义审计器面向已经存储完成的完整 trace 工作：它会在 `session_id` / `agent_id` / `user_id` 对应的轨迹上做回溯分析。这类能力适合用于合规复核、事故排查、事后分析，以及为前端生成总结性的风险等级。

公共 auditor 抽象位于：

```text
src/server/backend/audit/base.py
src/server/backend/audit/manager.py
src/server/backend/audit/registry.py
```

具体 auditor 实现需要放在：

```text
src/server/backend/audit/auditors/
```

后端发现并加载的 auditor 接口形态如下：

```python
from backend.audit.base import AuditResult, AuditTraceEntry, BaseAuditor
from backend.audit.registry import register


@register(
    name="my_trace_auditor",
    description="对已存储 trace 做风险等级总结。",
)
class MyTraceAuditor(BaseAuditor):
    def audit(
        self,
        trace: list[AuditTraceEntry],
    ) -> AuditResult:
        if any((record.get("decision") or {}).get("decision_type") == "deny" for record in trace):
            return AuditResult(level="high", reason="该轨迹中包含被拒绝的动作。")
        return AuditResult.ok()
```

每个 `AuditTraceEntry` 都对应一条规范化 trace 记录，包含 `session_id`、`agent_id`、`user_id`、`reason`、`event`、`decision`、`plugin_result`、`plugin_input`、`route` 和 `timestamp` 这些字段。对 auditor 来说，`event` 是主要运行时负载，其余字段则是后端 trace 管线补充的上下文信息。

`AuditResult` 当前统一使用四个等级：`critical`、`high`、`warning` 和 `ok`。每个结果还包含面向人的 `reason`，以及可选的 `metadata`。

当前类型定义在 `src/server/backend/audit/base.py`：

```python
@dataclass
class AuditResult:
    level: AuditLevel = "ok"
    reason: str = "No issue detected in trace."
    metadata: dict[str, Any] = field(default_factory=dict)
```

### AuditResult 字段说明

| 字段 | 类型 | 含义 | 什么时候填写 |
| --- | --- | --- | --- |
| `level` | `"critical" \| "high" \| "warning" \| "ok"` | auditor 输出的最终风险等级。前端或调用方通常先看这个字段决定该条审计结果严重程度。 | 当你要表达“这次 trace 最终风险有多高”时填写。一般 `ok` 表示未发现问题，`warning` 表示有可疑信号但未必需要立即处置，`high` 表示存在明显风险，`critical` 表示需要优先关注或立即阻断/升级。 |
| `reason` | `str` | 给人看的结论摘要，说明为什么给出这个等级。 | 基本都应该填写，建议写成一句完整的话，方便直接展示在 UI、日志或接口响应中。 |
| `metadata` | `dict[str, Any]` | 结构化补充信息，用来放前端、运维或二次处理程序还需要的上下文。 | 当你希望除了结论之外，再返回可机器读取的细节时填写，例如命中的事件 ID、风险标签、计数、工具名列表、用户列表等。 |

### AuditResult 常见写法

- 只需要返回结论时：

  ```python
  return AuditResult(level="high", reason="该轨迹中包含被拒绝的高风险操作。")
  ```

- 需要把审计细节返回给调用方时：

  ```python
  return AuditResult(
      level="warning",
      reason="发现可疑外发行为。",
      metadata={
          "event_ids": suspicious_event_ids,
          "risk_signals": sorted(risky_signals),
          "tool_names": sorted(tool_names),
      },
  )
  ```

- 没有发现问题时，可以直接返回：

  ```python
  return AuditResult.ok()
  ```

### AuditResult 辅助方法

| 成员 | 作用 | 什么时候使用 |
| --- | --- | --- |
| `AuditResult.ok(reason="No issue detected in trace.")` | 快速构造一个 `level="ok"` 的结果。 | 当 auditor 没有发现风险，想用最简洁的方式返回成功结果时使用。 |
| `result.to_dict()` | 把 `AuditResult` 转成可序列化字典，包含 `level`、`reason` 和 `metadata`。 | 当你要把结果返回给接口层、写测试快照、记录日志，或做额外 JSON 序列化时使用。 |

## AuditTraceEntry

`AuditTraceEntry` 是传入 `BaseAuditor.audit()` 的规范化记录类型。一条 entry 通常表示一个已存储的运行时事件，以及该事件对应的决策和检测元数据。

当前类型定义在 `src/server/backend/audit/base.py`：

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

### 字段说明

| 字段 | 类型 | 含义 | 如何使用 |
| --- | --- | --- | --- |
| `session_id` | `str` | 该 trace entry 所属的 session / run 标识。 | 用来分组或确认多条 entry 是否属于同一次运行。 |
| `agent_id` | `str or None` | 事件关联的智能体身份，如果可用则填写。 | 用来按 agent 维度限定审计结果，或写入结果 metadata。 |
| `user_id` | `str or None` | 事件关联的最终用户身份，如果可用则填写。 | 用来检测用户维度风险模式，或在报告中保留用户上下文。 |
| `reason` | `str or None` | 记录写入 trace 的原因，例如 `guard_decide`、`round_complete` 或 `client_error`。 | 用来区分正常远端判定、客户端本地缓存上传、异常路径同步等来源。 |
| `event` | `RuntimeEvent or None` | 标准化运行时事件，可以是 LLM 输入、LLM 输出、工具调用或工具结果。 | 这是 auditor 最常读取的主负载：事件类型、工具名、参数、结果、风险信号和 metadata 都在这里。 |
| `decision` | `GuardDecision or None` | 该事件对应的决策，如果存在则填写。 | 用来统计 deny / review，读取决策原因，或判断高风险动作是否已被阻断。 |
| `plugin_result` | `dict[str, Any]` | 该事件合并后的运行时检测结果，这里保存的是 plugin 风险元数据。 | 用来读取 `risk_signals`、检测 metadata，或运行时 plugin 附加的上下文。 |
| `plugin_input` | `dict[str, Any]` | plugin pipeline 接收到的输入载荷，如果 trace 来源记录了该信息则填写。 | 用来检查 plugin 当时看到的原始 event/context 载荷。 |
| `route` | `str or None` | 产生该 trace entry 的运行路径，如果有记录则填写。 | 用来区分远端判定、本地缓存上传或其他运行路径。 |
| `timestamp` | `float or None` | trace entry 的时间戳，如果有记录则填写。 | 用来排序记录，或在审计中计算时间窗口。 |

### 成员方法和属性

| 成员 | 作用 | 什么时候用 |
| --- | --- | --- |
| `AuditTraceEntry.from_dict(data)` | 从原始 trace 字典构造规范化 entry。它会尽量提取 `event`、`decision`、身份字段、`reason`、`plugin_result`、`plugin_input`、`route` 和 `timestamp`。 | 当 auditor 或测试拿到的是原始存储字典，而不是 `AuditTraceEntry` 对象时使用。 |
| `entry.to_dict()` | 将 entry 转成可序列化字典。如果存在 `event` 和 `decision`，会调用它们的 `to_dict()`。 | 用于调试、日志、测试快照，或返回规范化 trace 细节。 |
| `entry.merged_with(incoming)` | 将另一条 entry 合并进当前 entry，并返回新对象。incoming 中存在的身份、事件、决策、reason、route 和 timestamp 会优先使用；`plugin_result` 与 `plugin_input` 会做字典合并。 | 当服务端记录和客户端上传记录描述同一事件，需要合并为一条完整记录时使用。 |
| `entry.event_id` | 便捷属性，返回 `entry.event.event_id`；如果没有 event，则返回 `None`。 | 用于事件去重，或把 event id 写入审计结果 metadata。 |

### `event`、`decision` 和 `plugin_result`

这三个字段通常是 auditor 最主要的输入：

- `event: RuntimeEvent | None = None`

  `event` 是被审计的原始运行时事件。它说明“发生了什么”，包括事件类型、类型化 payload、上下文、风险信号和 adapter metadata。例如，`TOOL_INVOKE` 事件会暴露 `event.payload.tool_name`、`event.payload.arguments` 和 `event.payload.capabilities`；`LLM_INPUT` 事件会暴露 `event.payload.messages`；`LLM_OUTPUT` 事件会暴露 `event.payload.output`。

  当 auditor 需要检查实际运行行为时读取 `event`：

  ```python
  if entry.event and entry.event.event_type.value == "tool_invoke":
      tool_name = entry.event.payload.tool_name
      arguments = entry.event.payload.arguments
  ```

  如果存储的 trace record 中没有可解析的运行时事件，`event` 可能是 `None`，所以读取前需要先判断。

- `decision: GuardDecision | None = None`

  `decision` 是 AgentGuard 对该事件给出的决策。它说明运行时如何处理该事件，例如 allow、deny、review、degrade、sanitize 等。它还会携带决策原因、policy ID、风险信号和 metadata。

  当 auditor 需要汇总执行结果时读取 `decision`：

  ```python
  if entry.decision and entry.decision.decision_type.value == "deny":
      denied_event_ids.append(entry.event_id)
      reasons.append(entry.decision.reason)
  ```

  对于没有最终决策的上传 trace，或只携带部分运行上下文的 entry，`decision` 可能是 `None`。

- `plugin_result: dict[str, Any] = field(default_factory=dict)`

  `plugin_result` 保存运行时合并后的检测结果。常见内容包括 `risk_signals`、`metadata`、`is_final`，以及某些运行路径中的候选决策信息。

  当 auditor 需要查看最终决策之外的检测细节时读取 `plugin_result`：

  ```python
  signals = entry.plugin_result.get("risk_signals") or []
  metadata = entry.plugin_result.get("metadata") or {}
  ```

  与 `event` 和 `decision` 不同，这个字段始终是字典；如果没有保存 plugin 元数据，则为空字典。

- `plugin_input: dict[str, Any] = field(default_factory=dict)`

  `plugin_input` 保存 plugin pipeline 接收到的输入。如果 auditor 需要对比 plugin 当时看到的输入、规范化后的 `event` 和最终 `decision`，可以读取这个字段。

### 常见用法

大多数 auditor 会遍历完整 trace，并收集风险信号、决策、工具调用或身份信息：

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

编写 auditor 时，建议把 `event`、`decision`、`agent_id` 和 `user_id` 都当作可选字段处理。Trace 可能来自不同运行路径，做好 `None` 判断可以让 auditor 更稳健。

加入 auditor 实现后，后端会根据注册名自动发现它。此时前端可以：

- 调用 `GET /v1/backend/auditors` 列出当前可用 auditor 及其描述
- 调用 `POST /v1/backend/audit/custom/run`，传入 `session_id`、`agent_id`、`user_id` 和 `auditor_name`，对对应已存储 trace 执行一次审计

如果想看一个内置的具体例子，可参考 `src/server/backend/audit/auditors/trace_risk_summary.py`。
