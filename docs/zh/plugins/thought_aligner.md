# Thought-Aligner

`thought_aligner` 是一个默认关闭、按需启用的 server 侧 `llm_after` plugin。它会先扣住 Python agent 第一次生成的模型响应，把其中可获得的推理发送到 server 上配置的 Thought-Aligner 端点，然后让同一个 agent 模型基于安全 Thought 重新生成 `Action` 和 `Action Input`。第一次生成的 Action 不会先交给框架解析器或工具执行器。

实现遵循 [Thought-Aligner-7B 模型卡](https://huggingface.co/WhitzardAgent/Thought-Aligner-7B)给出的 prompt。商业使用前请确认该模型的 CC BY-NC 4.0 许可证是否适用。

## 执行流程

1. Python client 调用原 agent 模型，但 wrapper 暂不向 agent 返回第一次响应。
2. Client 标准化 LLM 输出，并告诉 server 当前这次请求的数据形态是否能安全回跳。
3. Server 构造：
   - 用户指令：优先使用显式 metadata，否则从 user/human 消息、嵌套输入或渲染后的 prompt 中提取；
   - 当前 Thought：依次尝试标准字段、常见 reasoning 别名、Thought 标签和 ReAct `Thought:` 文本；
   - 之前已经完成的 Thought/工具结果对，并用 `<thought> ... </thought>`、`<observation> ... </observation>` 标记。
4. Thought-Aligner 改写了 Thought 时，server 返回带安全 Thought 的 `align_thought` 决策。
5. Client 复制原请求，注入安全 Thought 和“只生成 Action/Final Answer”的指令，再调用一次原模型，只把重新生成的结果返回给 agent。重试事件会被标记，不会再次进入对齐循环。

Thought-Aligner 调用及其凭证始终在 AgentGuard server 侧。原 agent 模型仍由 client 调用，因为只有 client 持有原生模型 callable 以及具体框架的请求、响应对象。

## 配置

在 server 进程中设置独立环境变量。不要把密钥直接写入 JSON，也不要提交到仓库。

```bash
export THOUGHT_ALIGNER_BASE_URL="https://your-thought-aligner-host/v1"
export THOUGHT_ALIGNER_API_KEY="replace-with-a-secret"
export THOUGHT_ALIGNER_MODEL="thought-aligner-7b"
export AGENTGUARD_SERVER_PLUGIN_CONFIG="./config/plugins.thought-aligner.example.json"
```

然后正常启动 AgentGuard server。示例配置只在 server 的 `llm_after` 阶段启用该 plugin；现有 `config/plugins.json` 不变，所以默认行为不会开启 Thought-Aligner。

Python client 需要配置 server 地址，并确保远程决策超时大于 server plugin 的模型超时：

```python
from agentguard import AgentGuard

guard = AgentGuard(
    "agent-session",
    server_url="http://127.0.0.1:8000",
    remote_timeout_s=45,
    remote_retries=0,
)
guard.attach_langchain(agent)
```

如果某个框架传入的是不透明 prompt，server 无法稳定还原最初的用户任务，可以在受保护轮次开始前显式设置：

```python
guard.context.metadata["instruction"] = user_instruction
```

主要 plugin 参数：

- `timeout_s`：Thought-Aligner 端点超时，默认 `30` 秒。
- `failure_mode`：默认 `deny`；已经进入对齐的请求如果模型调用失败，会扣住第一次 Action。设置为 `allow` 可优先保证可用性，但模型失败后会释放原响应。
- `max_history_items`：最多传入的已完成 Thought/Observation 对，默认 `8`。
- `max_instruction_chars`、`max_thought_chars`、`max_observation_chars`：发送给外部模型前各字段的长度上限。

## 支持的输入与输出形式

Thought 提取不依赖具体框架类，目前识别：

- 标准化后的 `thought`；
- 嵌套的 `reasoning_content`、`reasoning`、`thinking`、`plan`、`analysis` 字段；
- `<think>`、`<thought>`、`<reason>`、`<reasoning>`、`<analysis>` 块；
- ReAct 文本中位于 `Action`、`Action Input`、`Observation` 或 `Final Answer` 之前的 `Thought:`。

Python 回跳当前支持非流式、已经被具体 patch 的 LLM 调用，请求形态可以是字符串、消息 dict/list/tuple、`input`/`prompt`/`messages` 参数，或 LangChain 风格 `agent_scratchpad`。对于常见字符串、字典和 Pydantic 消息响应，会尽量保留原生返回类型。

## 安全与兼容边界

- 如果模型供应商完全不暴露推理，AgentGuard 就没有真实 Thought 可供对齐。此时 plugin 不执行改写，也不会猜测或伪造隐藏思维链。
- 如果旧 client 或不支持回跳的 client 发送了包含 Thought 的事件，server 会在调用 Thought-Aligner 前拒绝该轮，因为无法证明 client 会重新生成 Action。
- 如果流式 agent 在 `llm_after` 之前就把包含 Action 的 chunk 发出，当前拦截路径无法提供保护。启用前需要缓冲完整一轮，或增加框架原生的 Action 前 hook。
- 每轮最多回跳一次；第二个 `align_thought` 决策会被阻断。
- 用户指令、可见 Thought 和选中的 Observation 会离开 AgentGuard server，发送到配置的模型端点。需要对该端点落实数据保留、脱敏和地域处理要求。server 的最终 decision metadata 只携带安全 Thought，不会再次复制原指令或不安全 Thought。
- Thought 对齐不能替代工具策略。重新生成的 Action 仍会经过 AgentGuard 原有的 `tool_before` 策略链。
