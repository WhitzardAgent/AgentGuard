# Thought-Aligner

`thought_aligner` is an opt-in `llm_after` server plugin. It holds a Python agent's first model response, sends the exposed reasoning to a server-hosted Thought-Aligner endpoint, and instructs the same agent model to regenerate `Action` and `Action Input` from the aligned thought before the first action can reach the framework's parser or tool executor.

The implementation follows the [Thought-Aligner-7B model-card prompt](https://huggingface.co/WhitzardAgent/Thought-Aligner-7B). Review that model's CC BY-NC 4.0 license before commercial use.

## Execution flow

1. The patched Python client calls the original agent model but keeps its response inside the wrapper.
2. The client normalizes the LLM output and tells the server whether that concrete request shape can be safely regenerated.
3. The server builds:
   - the user instruction, preferring explicit metadata and otherwise reading user/human messages or rendered prompts;
   - the current thought, preferring explicit normalized fields, then common reasoning aliases, thought tags, and ReAct `Thought:` text;
   - completed earlier thought/tool-result pairs, formatted as `<thought> ... </thought>` and `<observation> ... </observation>`.
4. If Thought-Aligner changes the thought, the server returns `align_thought` with the aligned thought.
5. The client copies the original request, injects the aligned thought plus an action-only instruction, calls the original model once more, and returns only the regenerated result. The retry is marked and cannot trigger another alignment loop.

The Thought-Aligner model call and its credentials stay on the AgentGuard server. The original agent model remains on the client side because only the client owns the native model callable and framework-specific request/response objects.

## Configuration

Set dedicated server environment variables. Do not put a key in JSON or commit it to the repository.

```bash
export THOUGHT_ALIGNER_BASE_URL="https://your-thought-aligner-host/v1"
export THOUGHT_ALIGNER_API_KEY="replace-with-a-secret"
export THOUGHT_ALIGNER_MODEL="thought-aligner-7b"
export AGENTGUARD_SERVER_PLUGIN_CONFIG="./config/plugins.thought-aligner.example.json"
```

Then start the AgentGuard server normally. The example file enables the plugin only in the server-side `llm_after` phase; the regular `config/plugins.json` remains unchanged, so Thought-Aligner is disabled by default.

If you prefer to enable it directly in `config/plugins.json`, add `thought_aligner` to `phases.llm_after.server`:

```json
{
  "phases": {
    "llm_before": {
      "client": [],
      "server": []
    },
    "llm_after": {
      "client": [],
      "server": [
        {
          "name": "thought_aligner",
          "env": {
            "base_url": "$THOUGHT_ALIGNER_BASE_URL",
            "api_key": "$THOUGHT_ALIGNER_API_KEY",
            "model": "$THOUGHT_ALIGNER_MODEL"
          },
          "kwargs": {
            "timeout_s": 30,
            "failure_mode": "allow",
            "max_history_items": 8,
            "max_instruction_chars": 12000,
            "max_thought_chars": 8000,
            "max_observation_chars": 12000
          }
        }
      ]
    },
    "tool_before": {
      "client": [],
      "server": [
        {
          "name": "rule_based_plugin",
          "env": {}
        }
      ]
    },
    "tool_after": {
      "client": [],
      "server": []
    }
  }
}
```

This keeps `thought_aligner` at the same level as other built-in plugins in the plugin config, while ensuring it only runs in the server-side `llm_after` phase.

The Python client needs a runtime-auth session for the server URL, plus a decision timeout longer than the server plugin's model timeout. For LangChain-style usage, prefer `Guard(..., ticket=...)` so AgentGuard can create the runtime session for you:

```python
from agentguard import Guard, Principal

guard = Guard(
    remote_url="http://127.0.0.1:8000",
    ticket="<AgentGuard User Ticket>",
    remote_timeout_s=45,
    remote_retries=0,
).start(principal=Principal(session_id="agent-session"))
guard.attach_langchain(agent)
```

Direct remote `AgentGuard(server_url=...)` construction without a runtime-auth `session_token` is no longer supported.

If a framework passes an opaque prompt and the server cannot reliably recover the original user task, provide it explicitly before the guarded turn:

```python
guard.context.metadata["instruction"] = user_instruction
```

Relevant plugin options:

- `timeout_s`: Thought-Aligner endpoint timeout; default `30` seconds.
- `failure_mode`: `deny` by default, which withholds the first action if an attempted alignment fails. `allow` preserves availability but releases the original response after a model failure.
- `max_history_items`: maximum completed thought/observation pairs; default `8`.
- `max_instruction_chars`, `max_thought_chars`, `max_observation_chars`: per-field bounds before the external model call.

## Supported input and output forms

Thought extraction is independent of framework classes and recognizes:

- normalized `thought`;
- nested `reasoning_content`, `reasoning`, `thinking`, `plan`, and `analysis` fields;
- `<think>`, `<thought>`, `<reason>`, `<reasoning>`, and `<analysis>` blocks;
- ReAct-style `Thought:` content before `Action`, `Action Input`, `Observation`, or `Final Answer`.

Python regeneration currently supports non-streaming, concrete patched LLM calls whose request is a string, a dict/list/tuple of chat messages, an `input`/`prompt`/`messages` argument, or a LangChain-style `agent_scratchpad`. Common string, dictionary, and Pydantic message responses retain their native shape where possible.

## Safety and compatibility boundaries

- A provider that does not expose reasoning gives AgentGuard no truthful Thought to align. In that case the plugin is a no-op; it never invents hidden chain-of-thought.
- A thought-bearing call from an old or unsupported client is denied before the Thought-Aligner call because that client cannot prove it will regenerate the action.
- Streaming agents that emit action-bearing chunks before `llm_after`, including current streaming-specific integrations, are not covered by this interception path. Buffer the complete turn or add a framework-native pre-action hook before enabling the plugin.
- The implementation performs at most one regeneration. A second `align_thought` directive is blocked.
- Instruction, exposed thought, and selected observations leave the AgentGuard server for the configured model endpoint. Apply your data-retention, redaction, and regional-processing requirements to that endpoint. The server decision metadata contains the aligned thought but does not copy the original instruction or unsafe thought into the decision.
- Thought alignment complements, but does not replace, AgentGuard tool policies. The regenerated action still passes through the existing `tool_before` policy path.
