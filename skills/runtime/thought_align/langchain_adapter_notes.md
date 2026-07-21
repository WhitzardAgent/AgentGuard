# LangChain Thought-Aligner Adapter Notes

This note records the LangChain-specific client changes required to make the
server-side `thought_aligner` plugin work end-to-end. Use it as a reference
when adapting other frameworks.

## Goal

When the server-side `thought_aligner` rewrites exposed reasoning, the client
adapter must:

- detect that the plugin returned a `loop_back_to_llm` decision
- rebuild a framework-native `llm_input`
- re-run the same model once with the aligned thought appended
- avoid re-triggering the aligner on the retry output
- preserve framework-native message and config objects well enough that the
  host framework still accepts the retried call

## LangChain-Specific Changes

### 1. Mark the client as thought-regeneration capable

File:

- `src/client/python/agentguard/adapters/agent/langchain.py`

Change:

- LangChain LLM input/output metadata now includes
  `thought_regeneration_supported = True`.

Reason:

- The server plugin checks this metadata before returning
  `loop_back_to_llm`.
- Without it, the plugin returns a blocking deny:
  `"Thought alignment requires client-side action regeneration support."`

### 2. Carry retry metadata so the plugin skips the retried output

File:

- `src/client/python/agentguard/adapters/agent/patching.py`

Change:

- When a `loop_back_to_llm` decision has
  `metadata.protocol == "thought_alignment_v1"`,
  `_loopback_metadata_from_decision(...)` returns:

```python
{"thought_alignment_attempt": 1}
```

- That metadata is merged into the retried `llm_input` and `llm_output`
  events through `extra_metadata`.

Reason:

- The server plugin checks `event.metadata["thought_alignment_attempt"]`.
- If present, it returns `retry_skipped` and does not align the retried
  `llm_output` again.

### 3. Treat `processed_content` as an aligned thought string

Files:

- `src/client/python/agentguard/adapters/agent/patching.py`
- `src/client/python/agentguard/adapters/agent/langchain.py`

Change:

- The server plugin still returns the aligned thought in
  `decision.processed_content`.
- LangChain `denormalize_llm_input(...)` interprets that string as the new
  assistant thought to inject into history.

Reason:

- The user explicitly preferred keeping `patching.py` payloads simple and
  doing framework-specific handling in the adapter.

### 4. Rebuild only `input`, not the full LangChain request envelope

File:

- `src/client/python/agentguard/adapters/agent/langchain.py`

Change:

- `_build_langchain_loopback_payload(...)` now returns only:

```python
{"input": rebuilt_input}
```

- It no longer normalizes and re-denormalizes the entire request, especially
  `config`.

Reason:

- LangChain `config` objects are not always plain dicts.
- Full request round-tripping could turn them into strings via `repr(...)`,
  which then caused errors such as:
  `AttributeError: 'str' object has no attribute 'copy'`.

### 5. Append the aligned thought as an assistant message

File:

- `src/client/python/agentguard/adapters/agent/langchain.py`

Change:

- `_inject_langchain_thought_message(...)` copies the previous model input and
  appends one assistant/ai message containing the aligned thought.

Meaning:

- If the original model input history was `A, B`, and the blocked model output
  produced aligned thought `B'`, the retried history becomes:

```text
A
B
B'
```

- It is not `A, B, C, B'`.
- It is not `A, B'`.
- The original blocked model output `C` is not injected back into history.

### 6. Preserve ToolMessage-required fields during message normalization

File:

- `src/client/python/agentguard/adapters/agent/langchain.py`

Change:

- Message normalization now preserves `tool_call_id` in addition to fields like
  `name`, `id`, `tool_calls`, and `response_metadata`.

Reason:

- LangChain reconstructs `ToolMessage` from normalized dicts.
- If `tool_call_id` is dropped, retry reconstruction fails with errors such as:
  `KeyError: 'tool_call_id'`.

### 7. Do not rely on client-side fail-open for aligner failure

Files:

- `src/server/backend/runtime/plugins/llm_after/thought_aligner.py`
- `src/client/python/agentguard/adapters/agent/patching.py`

Final state:

- The server plugin now defaults remote aligner failure to `allow`.
- Client-side `patching.py` no longer contains a special
  "thought-aligner deny but fail-open anyway" branch.

Reason:

- Server-side `allow + risk_signals + metadata` keeps audit semantics
  consistent.
- A server-side `deny` followed by a client-side silent allow produced
  confusing traces.

## Failure Cases Encountered During LangChain Adaptation

### Message coercion failure from blocked dict payloads

Symptom:

- LangGraph/LangChain attempted to treat
  `{"agentguard": "blocked", "reason": ...}` as a message.

Cause:

- Framework-native message channels expect `role/type + content`.

Resolution:

- Do not let thought-aligner remote failures surface as plugin-level `deny`
  under normal configuration.

### Config object corruption during loopback

Symptom:

- `AttributeError: 'str' object has no attribute 'copy'`

Cause:

- Retried LangChain `config` object was normalized into a string and then
  passed back as `config`.

Resolution:

- Rebuild only `input`; keep original `config` untouched.

### ToolMessage reconstruction failure

Symptom:

- `KeyError: 'tool_call_id'`

Cause:

- Loopback normalization dropped `tool_call_id` from tool messages.

Resolution:

- Preserve `tool_call_id` when normalizing LangChain message objects/dicts.

## General Adapter Pattern for Other Frameworks

If another framework needs Thought-Aligner support, copy these ideas:

1. Add a client metadata flag that explicitly says the framework adapter
   supports action regeneration.
2. Pass a retry marker into the retried LLM events so the server plugin skips
   the retry output.
3. Keep the server payload simple; let the adapter interpret the aligned thought
   and rebuild native input objects.
4. Rewrite only the minimum framework-native input field required to re-run the
   model.
5. Preserve framework-native message fields required for reconstruction.
6. Prefer server-side fail-open for remote aligner failure, not client-side
   post-hoc allow.

## Relevant Files

- `src/client/python/agentguard/adapters/agent/langchain.py`
- `src/client/python/agentguard/adapters/agent/patching.py`
- `src/server/backend/runtime/plugins/llm_after/thought_aligner.py`
- `src/server/backend/runtime/plugins/llm_after/thought_alignment.py`
- `tests/test_attach_adapters.py`
- `tests/test_qwen3guard_plugin.py`
