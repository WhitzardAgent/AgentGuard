# Qwen3Guard

AgentGuard 中的 `Qwen3Guard` 以一组内置安全分类 plugin 的形式提供，对应两个注册名：

- `qwen3guard_input`：运行在 `llm_before`，在模型调用前审查标准化后的 LLM 输入。
- `qwen3guard_output`：运行在 `llm_after`，在模型生成后审查标准化后的 LLM 输出。

这两个 plugin 同时支持部署在 client 和 server 两侧。它们会把当前的 LLM 输入或输出发送到兼容 Qwen3Guard 的 chat-completions 端点，解析返回结果中的 `Safety:` 和 `Categories:` 字段，再把分类结果转换成 AgentGuard 决策。

## 决策行为

- `safe`：不返回最终决策，继续后续链路。
- `unsafe`：返回最终 `DENY`，并附加 `qwen3guard_unsafe` 风险信号。
- `controversial`：返回最终 `HUMAN_CHECK`，并附加 `qwen3guard_controversial` 风险信号。
- `unknown`：返回最终 `HUMAN_CHECK`，并附加 `qwen3guard_unknown` 风险信号。

解析后的结果会记录到 `qwen3guard` 元数据中，包含 `safety`、`categories`、`raw_content` 和 `scope`。

## Client 侧配置方式

当你希望尽可能早地在智能体本地进程里拦截风险时，使用 client 侧配置。

```json
{
  "phases": {
    "llm_before": {
      "client": [
        {
          "name": "qwen3guard_input",
          "env": {
            "api_url": "https://your-qwen3guard-endpoint/v1/chat/completions",
            "api_key": "<YOUR_QWEN3GUARD_API_KEY>",
            "model": "Qwen3Guard-Gen-8B"
          },
          "temperature": 0,
          "timeout_s": 20
        }
      ],
      "server": []
    },
    "llm_after": {
      "client": [
        {
          "name": "qwen3guard_output",
          "env": {
            "api_url": "https://your-qwen3guard-endpoint/v1/chat/completions",
            "api_key": "<YOUR_QWEN3GUARD_API_KEY>",
            "model": "Qwen3Guard-Gen-8B"
          },
          "temperature": 0,
          "timeout_s": 20
        }
      ],
      "server": []
    }
  }
}
```

## Server 侧配置方式

当你希望集中式治理、共享审计可见性，或者采用 remote-only 部署模型时，使用 server 侧配置。

```json
{
  "phases": {
    "llm_before": {
      "client": [],
      "server": [
        {
          "name": "qwen3guard_input",
          "env": {
            "api_url": "https://your-qwen3guard-endpoint/v1/chat/completions",
            "api_key": "<YOUR_QWEN3GUARD_API_KEY>",
            "model": "Qwen3Guard-Gen-8B"
          },
          "temperature": 0,
          "timeout_s": 20
        }
      ]
    },
    "llm_after": {
      "client": [],
      "server": [
        {
          "name": "qwen3guard_output",
          "env": {
            "api_url": "https://your-qwen3guard-endpoint/v1/chat/completions",
            "api_key": "<YOUR_QWEN3GUARD_API_KEY>",
            "model": "Qwen3Guard-Gen-8B"
          },
          "temperature": 0,
          "timeout_s": 20
        }
      ]
    }
  }
}
```

## 这组 plugin 会检查什么

- `qwen3guard_input` 负责分类标准化后的 `llm_input` 消息。
- `qwen3guard_output` 负责分类标准化后的 `llm_output` 文本。
- 兼容的 Qwen3Guard 端点应返回 `Safety: safe|unsafe|controversial` 这类结果行，并可选返回 `Categories: ...`。
- 如果缺少 `api_url` 或 `api_key`，或者远端请求失败，plugin 会把错误写入 metadata，但不会产出最终决策。

## 什么时候选 Qwen3Guard

- 如果你想在 LLM 输入和输出两侧引入基于模型的安全分类，优先选 `Qwen3Guard`。
- 如果你需要的是一个轻量内置的 `llm_before` prompt injection 检测器，优先选 `jailbreak_check`。
- 如果你需要显式、可审计的工具调用策略，优先选 `rule_based_plugin`。
- 如果你需要在 server 侧的 `llm_after` 阶段改写可见推理，再决定后续动作，优先选 `thought_aligner`。
