# AgentGuard 适配 Dify 的两阶段方案

## Summary
默认按“两阶段”推进：先做低侵入的 Dify Service API adapter，快速保护 Dify app 的输入输出；再做 Dify 插件侧适配，拿到内部 LLM/tool 级别事件。依据：现有 AgentGuard adapter 模式在 [base.py](/home/dgroup/hjr/AgentGuard/src/client/python/agentguard/adapters/agent/base.py:52)、[patching.py](/home/dgroup/hjr/AgentGuard/src/client/python/agentguard/adapters/agent/patching.py:99)，Dify 官方 API/插件文档见 [Chat API](https://docs.dify.ai/api-reference/chats/send-chat-message)、[Workflow API](https://docs.dify.ai/api-reference/workflows/run-workflow)、[Tool Plugin](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/tool-plugin)、[Agent Strategy Plugin](https://docs.dify.ai/en/develop-plugin/dev-guides-and-walkthroughs/agent-strategy-plugin)。

## Key Changes
- 新增 `DifyAgentAdapter`，放在 `src/client/python/agentguard/adapters/agent/dify.py`，沿用 `BaseAgentAdapter`。
- 新增 `AgentGuard.attach_dify(client, *, app_type=None, wrap_llm=True, wrap_tools=False, stream_mode="buffered")`，并在 adapter `__init__` 中导出。
- Phase 1 只把 Dify app 调用归一成 `llm_input` / `llm_output`：
  - 支持方法名探测：`chat`、`send_chat_message`、`create_chat_message`、`chat_messages.create`、`run_workflow`、`workflow_run`、`workflows.run`、`completion`、`send_completion_message`、`completion_messages.create`。
  - `gettools()` 返回空列表；Dify 内部工具节点不在 API client 层伪造为 AgentGuard tool。
  - 请求归一化保留 `inputs`、`query`、`user`、`conversation_id`、`files`、`response_mode`、`app_type`、方法名和 owner 类型。
  - blocking 响应从 `answer`、`outputs`、`metadata`、`conversation_id`、`message_id`、`task_id` 中提取 `output/final_output`。
  - streaming 响应默认 `stream_mode="buffered"`：先收集 SSE/dict chunks，归一化 `message`、`agent_message`、`agent_thought`、`message_end`、`workflow_finished`、`node_finished`、`reasoning_chunk`，通过 `llm_output` 审核后再回放原 chunks；这样牺牲实时性但保证输出审核可执行。
  - 被 input guard 阻断时返回当前 AgentGuard 通用 marker dict：`{"agentguard": "blocked"|"pending"|"sanitized"|"degraded", ...}`，不伪造完整 Dify response。
- Phase 2 规划一个 Dify 插件侧集成：
  - 在 Dify Agent Strategy Plugin runner 中包住 `session.model.llm.invoke()`，生成 `llm_input/llm_output`。
  - 包住 `session.tool.invoke(provider, tool_name, parameters)`，生成 `tool_invoke/tool_result`，tool name 使用 `"{provider}.{tool_name}"`。
  - 能力标签默认 `["dify:tool", "provider:<provider>", "tool:<tool_name>"]`，允许用配置覆盖。
  - AgentGuard session metadata 使用 Dify 的 conversation/task/workflow run/app/plugin 信息，`server_url/api_key/plugin_config` 从环境变量或插件配置读取。

## Test Plan
- 在 `tests/test_attach_adapters.py` 增加 fake Dify client：
  - blocking chat 调用产生 `llm_input` 和 `llm_output`。
  - workflow blocking 从 `outputs` 提取 `final_output`。
  - streaming chat/workflow chunks 被 buffer、审核、按原顺序回放。
  - input deny 返回 AgentGuard marker，且不调用原 Dify client 方法。
- 增加 normalization 单测：
  - `agent_thought` 映射到 `thought`。
  - `agent_message/message` 拼接为最终输出。
  - `workflow_finished.data.outputs` 和 `node_finished.data.outputs` 可提取。
- 文档补充：
  - `pip install "agentguard[dify]"`。
  - `guard.attach_dify(client, app_type="chat")` 示例。
  - 明确 Phase 1 只能看到 app 级输入输出，内部工具可见性需要 Phase 2 插件侧接入。

## Assumptions
- 未收到你的范围选择回复，默认采用“两阶段方案”。
- Phase 1 不强依赖某个 Dify SDK 具体类名，以 duck typing 和方法名探测为主。
- streaming 默认安全优先，使用 buffered 模式；需要真实 token 流式体验时再显式开放 `stream_mode="passthrough"`。
