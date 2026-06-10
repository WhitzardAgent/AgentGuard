# AgentGuard Checkers

`checkers` 是 client 侧的本地检测层。它负责在事件进入策略判断前，对标准化后的 `RuntimeEvent` 做轻量、非网络的风险检测，并返回 `CheckResult`。

Checker 不直接执行工具，也不直接调用 LLM。它只读取事件内容，产出风险信号和可选的决策建议。

当前运行时只保留四类事件：

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

## BaseChecker

所有 checker 都应该继承 `BaseChecker`：

```python
class BaseChecker:
    name: str = "base"
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError
```

### 字段

`name`

Checker 的唯一或可读名称。`CheckerManager` 在捕获 checker 异常时，会用它写入 metadata，例如 `tool_invoke_error`。

`event_types`

这个 checker 关心的事件类型列表。为空时表示对所有事件都适用；通常建议显式声明，避免误跑到不相关阶段。

例如：

```python
event_types = [EventType.TOOL_INVOKE]
```

### 方法

`applies(event)`

判断当前 checker 是否应该处理这个事件。默认逻辑是：

- `event_types` 为空：适用于所有事件
- `event.event_type in event_types`：适用于匹配的事件

一般不需要重写，除非一个 checker 还要根据 payload 或 context 做更细粒度过滤。

`check(event, context)`

真正的检测逻辑。子类必须实现。它的输入是一个运行时事件和当前运行上下文，输出是 `CheckResult`。

client checker 当前只接收本次当前事件，不接收 `trajectory_window`。轨迹上下文会发送到
remote server，由 server 侧 checker / plugin / policy 使用。

## check() 的输入

### event: RuntimeEvent

`RuntimeEvent` 是 AgentGuard 内部统一后的事件对象，核心字段如下：

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

Checker 最常读取的是：

- `event.event_type`: 当前事件类型
- `event.payload`: 事件内容，不同阶段结构不同
- `event.risk_signals`: 已有风险信号
- `event.metadata`: 额外元信息

常见 payload 结构：

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

`RuntimeContext` 是当前 session 的上下文：

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

Checker 可以用它做和用户、agent、策略版本、环境相关的判断。

### trajectory_window

client checker 目前拿不到 `trajectory_window`。如果某个检测需要最近执行历史，应该放到
server 侧 checker 或 server plugin 中实现。

当 client checker 返回最终本地决策（`is_final=True`）时，client 会把 checker 的输入、
checker 结果、event、context 和 decision 写入本地同步缓存。下一次需要 remote decision
时，这些缓存会作为 `client_cached_entries` 一起发给 server；如果一整轮 LLM/工具调用都
没有依赖 remote decision，runtime 会在轮次结束后异步上传这些缓存，供 server 存储和审计。

## check() 的输出

`check()` 必须返回 `CheckResult`：

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

### decision_candidate

可选的决策建议，类型是 `GuardDecision`。

如果 checker 只是发现风险，不想直接决定，可以保持为 `None`。

如果 checker 发现必须阻断的情况，可以返回：

```python
GuardDecision.deny(
    "Destructive shell command blocked by local checker.",
    policy_id="local:dangerous_shell",
    risk_signals=["shell_command"],
)
```

### risk_signals

checker 检测到的风险标签列表，例如：

```python
["prompt_injection", "secret_detected", "external_send"]
```

`CheckerManager` 会合并所有 checker 返回的 `risk_signals`，去重后写回 `event.risk_signals`。

### is_final

表示这个 checker 的 `decision_candidate` 是否是最终本地决策。

- `False`: 只是一个候选建议，client 会把事件发送给 remote server，由 server 给出权威 decision
- `True`: checker 已经给出 client 侧最终决策，会跳过 remote server

通常只有确定性的高危规则才应该设置 `is_final=True`。

### metadata

附加调试或检测信息。`CheckerManager` 会把多个 checker 的 metadata 合并到最终 `CheckResult.metadata`。

## CheckerManager 如何调用 checker

Checker 按阶段配置和事件类型运行。不传 `checker_config` 时不会启用任何 checker。
一个典型的 client 配置如下：

```python
llm_before -> local ["llm_input"], remote []
llm_after -> local ["llm_output"], remote []
tool_before -> local ["tool_invoke"], remote []
tool_after -> local ["tool_result"], remote []
```

client 只会读取 `local` 列表；`remote` 列表由 server 侧 checker manager 使用。
配置必须使用 `{"phases": {...}}` 这一层结构。每个被配置的 phase 都必须同时包含
`local` 和 `remote`；不再接受 `{"llm_before": ["llm_input"]}` 这种旧格式。

事件到阶段的映射：

```python
LLM_INPUT -> llm_before
LLM_OUTPUT -> llm_after
TOOL_INVOKE -> tool_before
TOOL_RESULT -> tool_after
```

同一个阶段有多个 checker 时，按配置顺序依次调用。

如果某个 checker 抛异常，`CheckerManager` 会捕获异常，把错误写入 metadata，并继续执行后续 checker。checker 不应该打断主流程。

## 自定义 checker 示例

```python
from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.checkers.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="block_private_tool",
    description="Block calls to private/internal tools.",
)
class BlockPrivateToolChecker(BaseChecker):
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

配置示例：

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

然后在启动 client 时传入：

```python
guard = AgentGuard(
    session_id="s1",
    checker_config="/path/to/checkers.json",
)
```

client 运行过程中也可以替换 checker 配置：

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

新的配置会从下一次被 guard 的事件开始生效；已经完成检测的事件不会重新执行。

client 也可以暴露一个本地 HTTP endpoint 来更新运行时配置：

```python
url = guard.start_config_api()
# 默认: http://127.0.0.1:38181/v1/client/checkers/config
```

列出本地已经注册的 checker：

```bash
curl http://127.0.0.1:38181/v1/client/checkers/list \
  -H 'X-AgentGuard-Session-Key: sk-...'
```

返回：

```json
{
  "status": "ok",
  "checkers": [
    {
      "name": "llm_input",
      "description": "Detect prompt-injection and system-prompt leak attempts in LLM input.",
      "event_types": ["llm_input"]
    }
  ]
}
```

请求示例：

```bash
curl -X POST http://127.0.0.1:38181/v1/client/checkers/config \
  -H 'Content-Type: application/json' \
  -H 'X-AgentGuard-Session-Key: sk-...' \
  -d '{"config":{"phases":{"llm_before":{"local":["llm_input"],"remote":[]},"llm_after":{"local":[],"remote":[]},"tool_before":{"local":["tool_invoke"],"remote":[]},"tool_after":{"local":["tool_result"],"remote":[]}}}}'
```

client 本地 API 都需要 `X-AgentGuard-Session-Key`。这个值是 `AgentGuard`
初始化时的 `session_key`；如果没有显式传入，client 会自动生成一个 `sk-...`。

也可以传配置文件路径：

```json
{"path": "/path/to/checkers.json"}
```

也可以通过本地 API 上传新的 checker 代码：

```bash
curl -X POST http://127.0.0.1:38181/v1/client/checkers/update \
  -H 'Content-Type: application/json' \
  -H 'X-AgentGuard-Session-Key: sk-...' \
  -d '{
    "event_type": "llm_input",
    "filename": "my_llm_input_checker.py",
    "code": "from agentguard.checkers.base import BaseChecker, CheckResult\nfrom agentguard.checkers.registry import register\nfrom agentguard.schemas.events import EventType\n\n@register(name=\"my_llm_input\", description=\"My checker.\")\nclass MyLLMInputChecker(BaseChecker):\n    event_types = [EventType.LLM_INPUT]\n    def check(self, event, context):\n        return CheckResult(risk_signals=[\"my_signal\"])\n"
  }'
```

`event_type` 决定代码写入的位置：

- `llm_input` -> `checkers/llm_before/`
- `llm_output` -> `checkers/llm_after/`
- `tool_invoke` -> `checkers/tool_before/`
- `tool_result` -> `checkers/tool_after/`

写入后 client 会立即 import/reload 该模块，让 `@register(...)` 完成动态注册。
之后可以在 checker config 中直接使用新注册的 `name`。

## 新增 checker 时如何配置

新增 checker 时，把 checker 类放到对应阶段文件夹里，然后在 class 上添加
`@register(name=..., description=...)`。manager 会自动 discovery `agentguard.checkers`
下面的 checker 模块，让装饰器完成注册；配置文件里直接写注册的 `name` 即可。
使用这种方式，不需要修改 `__init__.py`，也不需要维护内置 checker map。

每个自定义 checker 还必须定义 `event_types`。它告诉 manager 这个 checker 适用于哪些
runtime event。可用值包括 `EventType.LLM_INPUT`、`EventType.LLM_OUTPUT`、
`EventType.TOOL_INVOKE` 和 `EventType.TOOL_RESULT`。

示例文件位置：

```text
agentguard/checkers/llm_before/my_checker.py
```

示例 checker：

```python
from agentguard.checkers.base import BaseChecker, CheckResult
from agentguard.checkers.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="my_checker",
    description="Short description of what this checker detects.",
)
class MyChecker(BaseChecker):
    event_types = [EventType.LLM_INPUT]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        return CheckResult.empty()
```

配置文件：

```json
{
  "phases": {
    "llm_before": {
      "local": [
        "my_checker"
      ],
      "remote": []
    }
  }
}
```

启动 client 时传入配置：

```python
guard = AgentGuard(
    session_id="s1",
    checker_config="/path/to/checkers.json",
)
```

关键是配置里写注册名：`my_checker`。checker 配置应该引用注册名。
