# Qwen3Guard

`Qwen3Guard` in AgentGuard is documented as a built-in safety-classifier plugin family with two registered plugin names:

- `qwen3guard_input`: runs in `llm_before` and reviews normalized LLM input before the model call.
- `qwen3guard_output`: runs in `llm_after` and reviews normalized LLM output after generation.

Both plugins are available on the client and the server. They send the current LLM input or output to a Qwen3Guard-compatible chat-completions endpoint, parse the returned `Safety:` and `Categories:` fields, and convert that classification into AgentGuard decisions.

## Decision behavior

- `safe`: continue the pipeline without a final decision.
- `unsafe`: return a final `DENY` and add the `qwen3guard_unsafe` risk signal.
- `controversial`: return a final `HUMAN_CHECK` and add the `qwen3guard_controversial` risk signal.
- `unknown`: return a final `HUMAN_CHECK` and add the `qwen3guard_unknown` risk signal.

The parsed result is stored in decision metadata under `qwen3guard`, including `safety`, `categories`, `raw_content`, and `scope`.

## Client-side configuration

Use the client-side form when you want the earliest possible intervention inside the agent process.

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

## Server-side configuration

Use the server-side form when you want centralized governance, shared audit visibility, or a remote-only deployment model.

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

## What the plugins inspect

- `qwen3guard_input` classifies the normalized `llm_input` messages.
- `qwen3guard_output` classifies the normalized `llm_output` text.
- A Qwen3Guard-compatible endpoint should return lines such as `Safety: safe|unsafe|controversial` and optionally `Categories: ...`.
- If `api_url` or `api_key` is missing, or the request fails, the plugin records the error in metadata and does not emit a final decision.

## When to use Qwen3Guard

- Choose `Qwen3Guard` when you want model-based safety classification around LLM prompts and responses.
- Choose `jailbreak_check` when you want a lightweight built-in prompt-injection detector in `llm_before`.
- Choose `rule_based_plugin` when you want explicit, auditable tool-call policies in `tool_before`.
- Choose `thought_aligner` when you want a server-side `llm_after` intervention that rewrites exposed reasoning before an action is executed.
