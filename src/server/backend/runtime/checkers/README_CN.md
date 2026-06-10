# Server Runtime Checkers

`backend.runtime.checkers` 是 server 侧的 checker 层。当 server 收到
`/v1/server/guard/decide` 请求时，它会先对请求里的 `current_event` 做本地检测，然后再进入
server plugin 和 policy 判断。

server checker 使用和 client 相同的事件模型。当前运行时只保留四类事件：

- `LLM_INPUT`
- `LLM_OUTPUT`
- `TOOL_INVOKE`
- `TOOL_RESULT`

## BaseChecker

所有 server checker 都继承 `BaseChecker`：

```python
class BaseChecker:
    name: str = "base"
    event_types: list[EventType] = []

    def applies(self, event: RuntimeEvent) -> bool:
        return not self.event_types or event.event_type in self.event_types

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        raise NotImplementedError
```

`check(event, context, trajectory_window=None)` 的输入是：

- `event`: 从请求 `current_event` 构造出来的标准化 `RuntimeEvent`
- `context`: 当前请求/session 的 `RuntimeContext`
- `trajectory_window`: client 请求传来的最近事件窗口

输出是 `CheckResult`：

```python
@dataclass
class CheckResult:
    decision_candidate: GuardDecision | None = None
    risk_signals: list[str] = field(default_factory=list)
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

`CheckerManager` 会合并所有 checker 的风险信号，写回 event，并在 server 响应中通过
`checker_result` 返回合并后的 checker 结果。

和 client checker 不同，server checker 可以查看 `trajectory_window`。适合做轨迹级判断，
比如“前面的 tool_result 读到了 secret，当前 tool_invoke 又尝试 external_send”。

`trajectory_window` 会由请求里的普通 `trajectory_window` 和 client 发来的
`client_cached_entries` 合并得到。`client_cached_entries` 是之前由 client checker
在本地做出最终决策、因此没有进入 server decision 的事件。server 也会通过
`/v1/server/trace/upload` 存储异步上传的缓存条目，供后续审计使用。

## 配置阶段

不传 `checker_config` 时不会启用任何 checker。一个典型的 server 配置如下：

```python
llm_before -> local [], remote ["llm_input"]
llm_after -> local [], remote ["llm_output"]
tool_before -> local [], remote ["tool_invoke"]
tool_after -> local [], remote ["tool_result"]
```

server 只会读取 `remote` 列表；`local` 列表由 client 侧 checker manager 使用。
配置必须使用 `{"phases": {...}}` 这一层结构。每个被配置的 phase 都必须同时包含
`local` 和 `remote`；不再接受 `{"tool_before": ["tool_invoke"]}` 这种旧格式。

事件到阶段的映射：

```python
LLM_INPUT -> llm_before
LLM_OUTPUT -> llm_after
TOOL_INVOKE -> tool_before
TOOL_RESULT -> tool_after
```

同一个阶段有多个 checker 时，按配置顺序依次调用。

## 新增 checker 时如何配置

新增 checker 时，把 checker 类放到对应阶段文件夹里，然后在 class 上添加
`@register(name=..., description=...)`。manager 会自动 discovery
`backend.runtime.checkers` 下面的 checker 模块，让装饰器完成注册；配置文件里直接写
注册的 `name` 即可。使用这种方式，不需要修改 `__init__.py`，也不需要维护内置
checker map。

server 的规则匹配也已经实现为 checker，位置是：

```text
backend/runtime/checkers/tool_before/rule_based_check/checker.py
```

它注册名是 `rule_based_check`。它是可选方案：只有在 checker 配置里启用这个注册名时，
server 才会执行 rule-based decision。如果通过 `RuntimeManager` 启用，它会绑定到
console 使用的同一份实时 policy store。

示例文件位置：

```text
backend/runtime/checkers/tool_before/my_checker.py
```

示例 checker：

```python
from backend.runtime.checkers.base import BaseChecker, CheckResult
from backend.runtime.checkers.registry import register
from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent


@register(
    name="my_server_checker",
    description="Short description of what this server checker detects.",
)
class MyServerChecker(BaseChecker):
    event_types = [EventType.TOOL_INVOKE]

    def check(
        self,
        event: RuntimeEvent,
        context: RuntimeContext,
        trajectory_window: list[RuntimeEvent] | None = None,
    ) -> CheckResult:
        return CheckResult.empty()
```

配置文件：

```json
{
  "phases": {
    "tool_before": {
      "local": [],
      "remote": [
        "tool_invoke",
        "my_server_checker"
      ]
    }
  }
}
```

关键是配置里写注册名：`my_server_checker`。checker 配置应该引用注册名。

## 如何加载配置

如果直接构造 server manager：

```python
from backend.runtime.manager import RuntimeManager

manager = RuntimeManager(checker_config="/path/to/server_checkers.json")
```

如果通过 FastAPI server 启动，设置环境变量：

```bash
export AGENTGUARD_SERVER_CHECKER_CONFIG=/path/to/server_checkers.json
```

或者：

```bash
export AGENTGUARD_CHECKER_CONFIG=/path/to/server_checkers.json
```

`AGENTGUARD_SERVER_CHECKER_CONFIG` 的优先级高于 `AGENTGUARD_CHECKER_CONFIG`。

也可以通过 backend API 在运行时更新 checker 配置：

```bash
curl -X POST http://127.0.0.1:8000/v1/backend/checkers/config \
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

backend 会先更新自己的 server checker manager。如果传入 `client_config_urls`，
backend 会继续向每个 client URL 转发 `{"config": ...}`，并在 `client_updates`
里返回每个 client 的更新结果。转发到 client 时，backend 会从 session pool
中查找该 URL 对应的 `client_key`，并携带 `X-AgentGuard-Session-Key`。如果该
client 尚未注册到 session pool，或 key 不匹配，client 会拒绝请求。如果 client
需要收到和 server 不同的配置，可以使用 `client_config`：

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
