# 自定义客户端插件

Client plugin 运行在智能体进程内，适合只依赖当前事件的低延迟检查，例如在工具调用离开 client 之前检查危险参数。

Client plugin 文件需要放在与事件阶段对应的目录中：

```text
src/client/python/agentguard/plugins/llm_before/
src/client/python/agentguard/plugins/llm_after/
src/client/python/agentguard/plugins/tool_before/
src/client/python/agentguard/plugins/tool_after/
```

## 输入

Client plugin 需要实现这个方法：

```python
def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
    ...
```

Client plugin manager 只会在当前事件阶段与配置阶段匹配，并且 `event_types` 允许该事件时调用 `check()`。

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
- `risk_signals`：前序 plugin 已经附加到事件上的风险标签。
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

Client plugin 不会收到 `trajectory_window`。如果检查逻辑需要同一个 session 的历史事件，应实现为 server plugin。

### 配置输入

Client plugin spec 从 `config/plugins.json` 或运行时 plugin config 的 `client` 列表读取：

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

- `name`：注册后的 plugin 名称。
- `env`：可选环境变量映射，类似 `$MY_PLUGIN_API_KEY` 的值会从进程环境变量中解析。
- `kwargs`：可选构造参数。
- 除 `name`、`env`、`kwargs` 之外的额外顶层字段，也会作为构造参数传入。

## 输出

`check()` 必须返回 `CheckResult`：

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

- `decision_candidate`：可选的 `GuardDecision` 建议。当 plugin 想返回 `ALLOW`、`DENY`、`SANITIZE`、`HUMAN_CHECK` 等决策时使用。
- `risk_signals`：当前 plugin 检测到的风险标签。manager 会去重并写回 `event.risk_signals`。
- `is_final`：表示 `decision_candidate` 是否是 client 侧最终决策。如果为 `True`，client 可以跳过该事件的 server decision 路径。只有确定性、高置信度检查才建议设置为 `True`。
- `metadata`：结构化调试信息或检测细节。manager 会把多个 plugin 的 metadata 合并到最终 plugin result 中。

没有发现风险时返回 `CheckResult.empty()`。

## 示例

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

配置示例：

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
