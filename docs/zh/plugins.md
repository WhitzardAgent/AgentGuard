# AgentGuard Plugins

AgentGuard 同时支持部署在 client 和 server 两侧的 plugin。两侧共享同一套标准化运行时 schema，但可见信息范围不同，部署位置也不同。若需要查看实现级细节，可参考 `../../src/client/python/agentguard/plugins/README_CN.md` 和 `../../src/server/backend/runtime/plugins/`。

## Client 与 Server Plugin 的区别

- **Client plugin** 运行在智能体进程本地，只接收当前 `event: RuntimeEvent` 和 `context: RuntimeContext`，适合低延迟、轻量级的本地过滤。
- **Server plugin** 运行在中控服务端，除了当前 `event` 和 `context`，还会接收到 `trajectory_window: list[RuntimeEvent]`，适合做跨步骤攻击链检测、集中策略评估与审计。
- Client plugin 文件需要放在 `../../src/client/python/agentguard/plugins/<phase>/`。
- Server plugin 文件需要放在 `../../src/server/backend/runtime/plugins/`。

## 内置 `rule_based_plugin` Plugin

AgentGuard 内置了一个名为 `rule_based_plugin` 的 server plugin。它面向基于规则配置的工具调用防护：用户可以手写 DSL 策略，也可以通过 UI 生成策略；该 plugin 会结合当前工具调用和近期 session 轨迹评估这些规则。当规则命中时，它可以识别对应安全风险，并在工具真正执行前返回 `DENY`、`HUMAN_CHECK` 或 `LLM_CHECK` 等决策。

在默认 quick start 流程中，`rule_based_plugin` 会作为 `tool_before` 阶段的远端 plugin 启用：

```json
{
  "phases": {
    "tool_before": {
      "local": [],
      "remote": [{"name": "rule_based_plugin", "env": {}}]
    }
  }
}
```

当你需要用明确、可审计的规则拦截 Shell 命令、非白名单外发请求，或阻止敏感数据流入邮件、HTTP、消息发送等工具时，优先使用这个 plugin。

## RuntimeEvent

`RuntimeEvent` 是 client 与 server plugin 共同使用的标准化事件对象：

```python
RuntimeEvent(
    event_id: str,
    event_type: EventType,
    timestamp: float,
    context: RuntimeContext,
    payload: dict[str, Any],
    risk_signals: list[str] = [],
    metadata: dict[str, Any] = {},
)
```

- `event_id`：当前运行时事件的唯一标识。
- `event_type`：当前事件所处的运行阶段，当前有效值包括 `LLM_INPUT`、`LLM_OUTPUT`、`TOOL_INVOKE` 和 `TOOL_RESULT`。
- `timestamp`：事件创建时间。
- `context`：挂载在该事件上的共享运行上下文。
- `payload`：plugin 实际要读取和判断的阶段数据。
- `risk_signals`：前序 plugin 已经附加到事件上的风险标签。
- `metadata`：事件附带的额外调试信息或 adapter 自定义信息。

常见的 payload 结构如下：

```python
# LLM_INPUT
{"messages": [...]}
{"text": "..."}  # 兼容/简化适配场景

# LLM_OUTPUT
{"output": ...}

# TOOL_INVOKE
{
    "tool_name": "send_email",
    "arguments": {"to": "...", "body": "..."},
    "capabilities": ["external_send"],
}

# TOOL_RESULT
{
    "tool_name": "read_file",
    "result": ...,
    "error": None,
}
```

## RuntimeContext

`RuntimeContext` 是在同一个 session 中跨事件传播的上下文对象：

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

- `session_id`：必填的会话标识，用来把同一次运行中的所有事件关联起来。
- `user_id`：可选，表示发起本次请求的最终用户身份。
- `agent_id`：可选，表示当前智能体实例或服务身份。
- `task_id`：可选，表示当前任务、工作流或执行单元的标识。
- `policy`：可选，表示当前会话关联的策略名称、来源或模式。
- `policy_version`：可选，表示策略版本号或快照标识。
- `environment`：可选，表示运行环境，例如 `dev`、`staging` 或 `prod`。
- `metadata`：自由扩展的附加上下文，例如租户信息、框架标签或 adapter 自定义字段。

## `trajectory_window: list[RuntimeEvent]`

`trajectory_window` 只会提供给 server 侧 plugin。

- 它表示同一个 session 的最近事件窗口。
- 列表中的每一个元素都是一个完整的 `RuntimeEvent`。
- 当检测逻辑依赖执行历史，而不是只看当前事件时，就应该使用它。
- 典型场景包括“前一个工具结果读出了敏感数据，后一个工具调用又尝试把它发送到外部”或“来自不可信 LLM 输出的内容最终流入 Shell 命令”。

Client plugin 拿不到 `trajectory_window`。如果你的检测逻辑依赖历史轨迹，就应该把它实现为 server plugin。实际运行时，server 看到的窗口既可能来自正常运行轨迹，也可能包含 client 后续同步上来的本地最终决策缓存。

## Custom Plugin

### Client-side plugin

Client plugin 需要放到与事件阶段对应的目录中：

```text
../../src/client/python/agentguard/plugins/llm_before/
../../src/client/python/agentguard/plugins/llm_after/
../../src/client/python/agentguard/plugins/tool_before/
../../src/client/python/agentguard/plugins/tool_after/
```

示例：

```python
from agentguard.plugins.base import BasePlugin, CheckResult
from agentguard.plugins.registry import register
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.events import EventType, RuntimeEvent


@register(
    name="my_client_plugin",
    description="Detect risky tool arguments on the client side.",
)
class MyClientPlugin(BasePlugin):
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event: RuntimeEvent, context: RuntimeContext) -> CheckResult:
        tool_name = event.payload.get("tool_name")
        arguments = event.payload.get("arguments") or {}
        if tool_name == "send_email" and arguments.get("to", "").endswith("@external.com"):
            return CheckResult(risk_signals=["external_send"])
        return CheckResult.empty()
```

### Server-side plugin

Server plugin 需要放到服务端 plugin 目录中：

```text
../../src/server/backend/runtime/plugins/
```

示例：

```python
from backend.runtime.plugins.base import BasePlugin, CheckResult
from backend.runtime.plugins.registry import register
from shared.schemas.context import RuntimeContext
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
        if trajectory_window and event.payload.get("tool_name") == "send_email":
            return CheckResult(risk_signals=["cross_step_review"])
        return CheckResult.empty()
```

Server 侧 plugin 目录为 `../../src/server/backend/runtime/plugins/`。

### Plugin 配置

加入 plugin 类之后，需要在 plugin 配置中用 plugin spec 对象引用它们。`name` 字段是注册名。对于 client 侧 `local` plugin，`env`、`kwargs` 和顶层构造参数都会传入 plugin 实例；对于 server 侧 `remote` plugin，当前运行时只会按 `name` 或 `class` 解析 plugin，不会把 `env`/`kwargs` 注入构造函数。

```json
{
  "phases": {
    "tool_before": {
      "local": [
        {
          "name": "my_client_plugin",
          "env": {}
        }
      ],
      "remote": [
        {
          "name": "rule_based_plugin",
          "env": {}
        },
        {
          "name": "my_server_plugin",
          "env": {}
        }
      ]
    }
  }
}
```

- `local` 由 client 侧 plugin manager 加载。
- `remote` 由 server 侧 plugin manager 加载。
- `local` plugin spec 可以使用 `name`、可选的 `env`，也可以通过 `kwargs` 或顶层字段传入构造参数。
- `remote` plugin spec 当前主要使用 `name`（或 `class`）做解析；额外字段会保留在配置里，但不会被注入 server plugin 构造函数。
- 即使两个 plugin spec 出现在同一份配置文件里，对应实现文件仍然必须分别部署到正确的 client 或 server 目录下。
