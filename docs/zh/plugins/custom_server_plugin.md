# 自定义服务端插件

Server plugin 运行在 AgentGuard 中控服务端，适合集中策略决策、跨步骤检测，以及需要最近 session 历史事件的检查。

Server plugin 文件需要放在与事件阶段对应的目录中：

```text
src/server/backend/runtime/plugins/llm_before/
src/server/backend/runtime/plugins/llm_after/
src/server/backend/runtime/plugins/tool_before/
src/server/backend/runtime/plugins/tool_after/
```

## 输入

Server plugin 需要实现这个方法：

```python
def check(
    self,
    event: RuntimeEvent,
    context: RuntimeContext,
    trajectory_window: list[RuntimeEvent] | None = None,
) -> CheckResult:
    ...
```

Server plugin manager 只会在当前事件阶段与配置阶段匹配，并且 `event_types` 允许该事件时调用 `check()`。

### `event: RuntimeEvent`

`event` 是 plugin 要检查的标准化运行时事件：

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

- `event_id`：事件唯一标识。
- `event_type`：当前事件类型，支持 `LLM_INPUT`、`LLM_OUTPUT`、`TOOL_INVOKE` 和 `TOOL_RESULT`。
- `timestamp`：事件创建时间。
- `context`：同一个 `check()` 调用中传入的运行上下文。
- `payload`：四个类型化 payload 类之一：`LLMInput`、`LLMOutput`、`ToolInvoke` 或 `ToolResult`。
- `risk_signals`：client plugin、预处理器或前序 server plugin 已经附加到事件上的风险标签。
- `metadata`：adapter 或运行时附加的调试信息。

常见 payload 结构：

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

`context` 描述当前 session 与 agent 身份：

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

- `session_id`：必填的会话标识。
- `user_id`：可选，最终用户身份。
- `agent_id`：可选，当前 agent 实例或服务身份。
- `task_id`：可选，工作流或任务标识。
- `policy`：可选，策略名称、来源或模式。
- `policy_version`：可选，策略版本或快照标识。
- `environment`：可选，运行环境，例如 `dev`、`staging` 或 `prod`。
- `metadata`：自由扩展的额外上下文。

### `trajectory_window: list[RuntimeEvent] | None`

`trajectory_window` 只提供给 server plugin。

- 它包含同一个 session 最近发生的事件。
- 每个元素都是完整的 `RuntimeEvent`。
- 它也可能包含 client 侧缓存并同步到 server 的 plugin decision。
- 它适合跨步骤检测，例如“前一个工具结果读取了敏感数据，后一个外发工具调用尝试发送这些数据”。

建议始终兼容 `None`：

```python
trajectory_window = trajectory_window or []
```

### 配置输入

Server plugin spec 从 `config/plugins.json` 或运行时 plugin config 的 `server` 列表读取：

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

- `name`：注册后的 plugin 名称。
- `class` 或 `plugin`：也可以作为 `name` 的替代形式，用来写导入路径。
- 当前 server runtime 会按 `name` 或导入路径解析 plugin 类。
- 额外字段会保留在配置中，但当前 server plugin manager 不会把 `env` 或 `kwargs` 注入 server plugin 构造函数。

## 输出

`check()` 必须返回 `CheckResult`：

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
```

- `decision_candidate`：可选的 `GuardDecision` 建议。当 plugin 想返回 `ALLOW`、`DENY`、`SANITIZE`、`HUMAN_CHECK` 等决策时使用。
- `risk_signals`：当前 plugin 检测到的风险标签。manager 会去重并写回 `event.risk_signals`。
- `is_final`：表示 `decision_candidate` 是否是权威 server 侧决策，默认值为 `True`。如果为 `True`，runtime 可以直接使用这个决策。
- `metadata`：结构化调试信息或检测细节。manager 会把多个 plugin 的 metadata 合并到最终 plugin result 中。

没有发现风险时返回 `CheckResult.empty()`。

## 示例

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

配置示例：

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
