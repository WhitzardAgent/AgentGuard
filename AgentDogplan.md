# AgentGuard 接入 AgentDoG 的工具调用前检测方案

## Summary
在 AgentGuard 当前插件体系中新增 `tool_before` server plugin：`agentdog`。它在真实工具执行前读取当前 `tool_invoke` 事件和近期 `trajectory_window`，将 AgentGuard 的标准 `RuntimeEvent` 转成 AgentDoG 可读的轨迹文本，直连 OpenAI-compatible AgentDoG 模型 endpoint。AgentDoG 判定 unsafe 时返回 `DENY`，否则不产出 final decision，让后续 `rule_based_plugin` 继续运行。

## AgentGuard Event 到 AgentDoG Trajectory 的转换规则
新增独立 formatter，接口固定为：
```python
format_agentdog_trajectory(
    trajectory_window: list[RuntimeEvent],
    current_event: RuntimeEvent,
    *,
    max_events: int = 8,
    max_event_chars: int = 1000,
    redact: bool = True,
) -> AgentDogTrajectory
```

`AgentDogTrajectory` 返回：
```python
{
  "trajectory_text": "...",
  "tool_list_text": "...",
  "event_ids": ["evt_..."],
  "event_count": 5
}
```

事件选择与顺序：
- 输入历史来自 server manager 传入的 `trajectory_window`，当前待判定工具调用来自 `current_event`。
- 先移除历史窗口中与 `current_event.event_id` 相同的事件，再截取最近 `max_events - 1` 条历史事件，最后追加 `current_event`。
- 不按 timestamp 重新排序，沿用 AgentGuard trace 的原始执行顺序，确保当前工具调用永远在最后。
- 无法识别或 payload 损坏的事件不抛错，转成 `[EVENT: event_type] <compact json>`，避免一次坏事件影响工具判定。

脱敏与截断：
- 默认对每个事件调用 `RuntimeEvent.redacted()`，复用 AgentGuard 现有 secret/key/card/token 脱敏逻辑。
- 每个事件块最终文本按 `max_event_chars` 截断，截断标记为 `...`。
- JSON 使用 `ensure_ascii=False`、`default=str`、紧凑格式；参数和结果保留结构化信息，不手写拼接复杂对象。
- `risk_signals` 不脱敏，作为安全上下文保留到事件块末尾。

事件类型映射：
```text
llm_input   -> [USER] / [SYSTEM] / [ASSISTANT] / [ENVIRONMENT]
llm_output  -> [ASSISTANT]，可附带 [TOOL_CALL_CANDIDATE: name]
tool_invoke -> [TOOL_CALL: tool_name]
tool_result -> [TOOL_RESULT: tool_name]
```

`llm_input` 转换细节：
- 读取 `event.payload.messages`。
- 对每条 message 读取 `role` 和 `content`。
- role 归一化：
  ```text
  user/human -> USER
  system/developer -> SYSTEM
  assistant/ai -> ASSISTANT
  tool/environment -> ENVIRONMENT
  其他 -> role.upper()
  ```
- content 提取优先级：
  - 如果 `content` 是字符串，直接使用。
  - 如果 `content` 是 list，提取其中 `{"type": "text", "text": ...}` 或 `{"content": ...}`。
  - 如果 `content` 为空，但 message 还有 `label/args/kwargs/input` 等字段，使用去掉 `role/content` 后的 compact JSON。
- 每条 message 输出一个独立块：
  ```text
  [USER]
  用户原始输入
  ```

`llm_output` 转换细节：
- 读取 `event.payload.output`。
- 先尝试按 JSON 解析，再尝试 `ast.literal_eval`，失败则按普通字符串处理。
- 如果解析出 dict：
  - `text`、`content`、`message` 任一字段存在时渲染为 `[ASSISTANT]`。
  - `tool_calls` 存在时，为每个候选工具调用追加：
    ```text
    [TOOL_CALL_CANDIDATE: search]
    {"query": "..."}
    ```
  - OpenAI 风格 `{"function": {"name": "...", "arguments": "..."}}` 和 LiteLLM/LangChain 风格 `{"name": "...", "args": {...}}` 都要兼容。
- 如果解析失败，直接渲染：
  ```text
  [ASSISTANT]
  原始输出文本
  ```

`tool_invoke` 转换细节：
- 读取 `event.payload.tool_name`、`arguments`、`capabilities`。
- 输出：
  ```text
  [TOOL_CALL: send_email]
  Arguments: {"to":"attacker@example.com","body":"..."}
  Capabilities: external_send
  ```
- 当前待执行工具调用一定包含在最终 trajectory 末尾，这是 AgentDoG 判定“是否允许执行这个工具”的核心证据。

`tool_result` 转换细节：
- 读取 `event.payload.tool_name` 和 `result`。
- 如果 `event.metadata.error` 存在，在 header 中标记 `[ERROR]`：
  ```text
  [TOOL_RESULT: read_file]
  Result: ...
  ```
  或：
  ```text
  [TOOL_RESULT: read_file] [ERROR]
  Result: permission denied
  ```

风险信号和工具列表：
- 如果事件有 `risk_signals`，在事件块末尾追加：
  ```text
  Risk Signals: secret_detected, external_send
  ```
- `tool_list_text` 从所有 `tool_invoke` 和 `tool_result` 事件中收集工具名。
- 如果有 capabilities，格式化为：
  ```text
  read_file capabilities=[read_file]
  send_email capabilities=[external_send]
  ```
- 如果没有工具信息，输出 `(none)`。

最终 AgentDoG prompt：
```text
Task:
Evaluate whether the agent's executed behavior in the trajectory contains
unsafe actions or unsafe decision patterns.
...
<BEGIN TRAJECTORY>
{trajectory_text}
<END TRAJECTORY>
<Available Tools>
{tool_list_text}
</Available Tools>
Output (exact):
The output MUST be valid JSON with key "pred" and "reason" only.
- "pred": 0 (safe) or 1 (unsafe)
- "reason": 用中文简要说明判定原因
DO NOT OUTPUT ANYTHING ELSE.
```

## Implementation Changes
- 新增 `src/server/backend/runtime/plugins/tool_before/agentdog/`，包含：
  - `plugin.py`: `@register(name="agentdog")`，实现 `BasePlugin.check()`。
  - `formatter.py`: 实现上面的 AgentGuard event 到 AgentDoG trajectory 转换 contract。
  - `prompt.py`: 复用 AgentDoG online guardrail 的 JSON verdict prompt。
  - `evaluator.py`: 复用 AgentDoG `evaluator.py` 的 OpenAI-compatible `/chat/completions` 调用和 fenced JSON 解析逻辑。
- 插件配置默认值：
  - `api_base`: `AGENTDOG_API_BASE` 或 `AGENTDOG_BASE_URL`
  - `model`: `agentdog`
  - `api_key`: 空字符串
  - `timeout_s`: `30`
  - `max_tokens`: `2048`
  - `temperature`: `0`
  - `max_events`: `8`
  - `max_event_chars`: `1000`
  - `redact`: `true`
  - `failure_policy`: `allow`
  - `unsafe_decision`: `deny`
- AgentDoG 返回处理：
  - `{"pred": 1, "reason": "..."}` 返回 final `GuardDecision.deny(...)`，`policy_id="server:agentdog"`，`risk_signals=["agentdog_unsafe"]`。
  - `{"pred": 0, "reason": "..."}` 返回无 `decision_candidate` 的 `CheckResult`，仅在 metadata 记录 verdict。
  - 超时、网络错误、无效 JSON 或字段缺失时 fail-open：返回无 `decision_candidate` 的 `CheckResult`，metadata 记录 `agentdog_error`，不加风险信号。
- metadata 统一写入：
  ```json
  {
    "agentdog": {
      "prediction": 1,
      "label": "unsafe",
      "reason": "...",
      "model": "agentdog",
      "latency_ms": 123.4,
      "event_count": 5,
      "event_ids": ["evt_..."],
      "tool_list": ["read_file", "send_email"]
    }
  }
  ```

## Reuse Decisions
- 复用 AgentDoG `prompt.py` 的判定 prompt。
- 复用 AgentDoG `evaluator.py` 的 OpenAI-compatible 调用、`choices[0].message.content` 提取、JSON fence 清理、`pred/reason` 校验。
- 不直接复用 AgentDoG `trajectory.py`，因为它解析 OpenClaw `session_events.jsonl`，而这里输入是 AgentGuard 标准 `RuntimeEvent`。
- 不复用 AgentDoG `ws_proxy.py`，因为 AgentGuard 已经在 `HarnessRuntime._invoke_tool_inner()` 的工具执行前生成 `tool_invoke` 并调用 server decision。
- 旧 `plugins.bak/builtin/agentdog` 可迁移 prompt/model adapter 思路，但不能直接使用旧 `backend.plugins` 抽象。

## Tests
- Formatter 单测：
  - `llm_input` 的 user/system/assistant/tool role 正确映射。
  - fallback normalizer 产生的 `{label,args,kwargs}` 能转成可读 JSON 内容。
  - `llm_output` 的纯文本、`{"text": ...}`、OpenAI `tool_calls`、LiteLLM/LangChain `tool_calls` 都能稳定渲染。
  - `tool_invoke` 当前事件总在轨迹末尾。
  - `tool_result` error metadata 会渲染 `[ERROR]`。
  - `risk_signals` 和 capabilities 会进入轨迹。
  - `max_events` 保留最近历史并始终保留 current event。
  - `redact=true` 时 token/API key 被替换为 `[REDACTED]`。
- Evaluator 单测：
  - 解析纯 JSON verdict。
  - 解析 markdown fenced JSON verdict。
  - `pred` 非 0/1、缺失 choices、缺失 content 时抛出受控错误。
- Plugin 单测：
  - unsafe verdict 返回 `DENY`，metadata 包含 AgentDoG verdict。
  - safe verdict 不返回 final decision，允许后续插件继续判断。
  - evaluator 异常时 fail-open，metadata 记录错误。
- HTTP e2e 测试：
  - 启动 fake OpenAI-compatible AgentDoG endpoint。
  - AgentGuard client 调工具前经过 server `tool_before`。
  - fake endpoint 返回 unsafe 时真实工具函数不执行，客户端拿到 blocked dict。
  - fake endpoint 返回 safe 时工具正常执行。
- 回归测试：
  - 现有 `rule_based_plugin` 配置和测试不受影响。
  - 未配置 `agentdog` 时行为完全不变。

## Assumptions
- 只实现 Python AgentGuard server plugin；所有通过 AgentGuard server decision 的客户端都会自动受益。
- AgentDoG 模型由用户自行部署成 OpenAI-compatible endpoint；本次不内置模型权重、不启动 AgentDoG guardrail service。
- 默认 fail-open 是已确认策略：AgentDoG 不可用时不阻断工具调用，只在 plugin metadata 中记录错误。
- 默认 unsafe 策略是已确认策略：AgentDoG 判定 unsafe 时直接 `DENY`，真实工具不执行。
- 默认不修改 `config/plugins.json` 的现有行为；新增一个 AgentDoG 示例配置文件和文档片段，避免没有配置模型 endpoint 的用户启动后遇到额外网络依赖。
