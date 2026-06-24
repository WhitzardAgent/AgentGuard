# Custom Adapter

If your agent framework does not yet have a built-in AgentGuard adapter, you can add a custom adapter and connect it to Guard.

This page sits alongside the LangChain, AutoGen, and OpenAI Agents SDK integration pages, but it focuses on one thing: how to implement a new adapter yourself.

## What this page is for

There used to be a separate `Agent Adapter Contract` page. Its purpose was very narrow: **define what an adapter is responsible for inside AgentGuard, which parts must be implemented by the adapter, and which parts can be reused from `BaseAgentAdapter`.**

Instead of keeping that as a separate page, this section folds the same ideas into `Custom Adapter`: first clarify the adapter boundary, then move into implementation patterns, example code, and Guard integration.

## Adapter responsibilities

An adapter does not make policy decisions by itself. Its main job is to translate framework-native call sites into bindings that AgentGuard can process uniformly:

- locate tool invocation entry points
- locate LLM invocation entry points
- describe them as `ToolBinding` / `LLMBinding`
- hand them off to `BaseAgentAdapter` for shared patching and event wiring

In practice, a new adapter usually needs to implement at least:

- `can_wrap(...)`: decide whether the adapter applies to the agent
- `gettools(...)`: return tool-call bindings
- `getllm(...)`: return model-call bindings
- `generate(...)`: provide a best-effort single-turn execution entry

## What the base class already handles

On the Python client, custom adapters are usually built on top of `BaseAgentAdapter`.

In most cases, you do not need to reimplement `patchtool(...)` or `patchLLM(...)`. The base class already:

1. calls `gettools(...)` / `getllm(...)`
2. stores the results in `self.toolslist` / `self.llms`
3. wraps and installs each binding automatically
4. emits the shared runtime guard events around each call

So for many frameworks, the real adapter work is not writing wrapper logic from scratch, but translating framework-native objects into binding lists.

## What `ToolBinding` and `LLMBinding` represent

Each item returned by `gettools(...)` is a `ToolBinding`. Its three core fields are:

- tool name `name`
- parameter description `parameters`
- the real callable `callable`

It can also include:

- `owner` / `attr`: where the callable is mounted on an object
- `container` / `key`: where the callable lives inside a `list` / `dict`
- `tool` / `capabilities`: extra tool metadata
- `installer`: a custom installation hook when the default patch flow is not enough

Each item returned by `getllm(...)` is an `LLMBinding`. Its core fields are:

- `label`
- `callable`

and it can also carry:

- `owner` / `attr`
- `container` / `key`
- `installer`

## Minimal implementation steps

1. inherit `BaseAgentAdapter`
2. implement `can_wrap(...)`
3. implement `gettools(...)`
4. implement `getllm(...)`
5. implement `generate(...)`
6. override normalization only if your framework needs it
7. use `adapter.attach(agent, guard)` or add a convenience method on `Guard`

## How to implement `can_wrap(...)`

The goal of `can_wrap(...)` is not to guess whether an object might be runnable. Its job is **to reliably decide whether this adapter is the right adapter for the given agent object**.

Good matching strategies usually include:

- checking whether `type(agent).__module__` contains a stable framework signature
- checking whether the agent exposes a stable set of key attributes
- combining module identity and object structure for a stricter match

For example:

```python
def can_wrap(self, agent: Any) -> bool:
    mod = type(agent).__module__ or ""
    return "myframework" in mod and hasattr(agent, "tools") and hasattr(agent, "model")
```

A few practical guidelines:

- be conservative; avoid matching objects that belong to other frameworks
- prefer stable identifiers over temporary runtime attributes
- if multiple adapters could match similar objects, make `can_wrap(...)` more specific
- for fully custom project-local agents, structure-based matching alone can still be enough

You can think of `can_wrap(...)` as the adapter's recognizer. The more precise it is, the safer patching becomes.

## How to implement `gettools(...)`

`gettools(...)` collects tool entry points and turns them into `list[ToolBinding]`.

The core question it answers is: **where is the real callable that actually executes tool logic in this framework?**

Common sources include:

- tool lists like `agent.tools`
- name-to-tool maps like `agent.tools_by_name`
- registries such as `function_map`
- deferred registration APIs such as `register_function(...)`
- concrete execution methods like `func`, `_func`, `run_json`, `invoke`, `_run`, or `coroutine`

In simple cases, you can reuse the helpers already provided by the base class:

```python
def gettools(self, agent: Any) -> list[ToolBinding]:
    bindings: list[ToolBinding] = []

    tools = getattr(agent, "tools", None)
    if isinstance(tools, list):
        bindings.extend(self.collect_tool_list(tools, func_attrs=("func", "_func")))

    registry = getattr(agent, "function_map", None)
    if isinstance(registry, dict):
        bindings.extend(self.collect_function_map(registry))

    if hasattr(agent, "register_function"):
        bindings.extend(self.collect_register_function(agent))

    return bindings
```

The most important design choice in `gettools(...)` is selecting the right patch point:

- if a tool exposes both `invoke(...)` and a lower-level `func(...)`, prefer the one that preserves the real business arguments
- if the public entry point wraps everything into a generic `input`, patching the lower-level function often gives better guard visibility
- if only the public entry point is available, patch it and recover the real arguments in normalization
- if the framework supports dynamic tool registration, patch both existing tools and the registration entry point

Each returned `ToolBinding` should answer three things clearly:

- what the tool is called
- which callable is the real execution entry point
- where the wrapped callable should be installed back

If the default installation logic is not enough, attach a custom `installer` to the binding.

## How to implement `getllm(...)`

`getllm(...)` collects model invocation entry points and turns them into `list[LLMBinding]`.

The key is to find **the callable that actually sends the model request**, not just a higher-level business wrapper.

Typical entry points include:

- `model.invoke(...)`
- `client.create(...)`
- `chat.completions.create(...)`
- `messages.create(...)`
- `create_stream(...)`
- deep client paths such as `_client.xxx` in some frameworks

If the target object and method paths are clear, `collect_llm_methods(...)` is often enough:

```python
def getllm(self, agent: Any) -> list[LLMBinding]:
    model = getattr(agent, "model", None)
    if model is None:
        return []
    return self.collect_llm_methods(model, methods=("create", "invoke", "chat"))
```

The `label` field acts as the logical name of that LLM entry point. It is useful in:

- `llm_input` payloads or metadata
- trace records when distinguishing different model paths
- debugging when one framework exposes multiple LLM call surfaces

Implementation tips:

- if one request flows through multiple wrappers, avoid patching it in a way that creates duplicate `llm_input` / `llm_output` pairs
- if the framework supports sync, async, and streaming variants, collect each real call surface explicitly
- if different providers expose different client paths, branch on client type the way the AutoGen adapter does

In short, `getllm(...)` is how you expose the real model call site to AgentGuard.

## What the normalization hooks do

Normalization converts framework-native objects into stable event payloads that AgentGuard runtime can consume consistently.

The base class already provides a minimal default implementation. If your framework mostly passes plain Python values around, you may not need to override anything.

But once a framework wraps messages, tool arguments, or results inside deeper objects, custom normalization becomes important.

### `normalize_llm_input(...)`

This hook converts the pre-call LLM request into a normalized payload.

The default implementation usually preserves:

- `label`
- `args`
- `kwargs`
- basic metadata

Override it when:

- the real messages live inside `kwargs["messages"]`, `kwargs["input"]`, or framework-specific objects
- you want to flatten message objects into a stable `{role, content}` shape
- you want to add extra metadata such as model name, provider name, or owner type

This directly affects what Guard sees in the `llm_input` event.

### `normalize_llm_output(...)`

This hook converts the LLM return value into a normalized output payload.

The default implementation usually:

- converts values into primitives, `dict`, or string representations when possible
- adds basic output metadata

Override it when:

- the framework returns a complex response object that would otherwise degrade to `str(...)`
- you want to preserve structured fields like `content`, `tool_calls`, `response_metadata`, or `usage`
- you need to distinguish plain text output, message objects, and streaming chunks more clearly

This affects the quality and usefulness of the `llm_output` event.

### `normalize_tool_invoke(...)`

This hook converts tool-call arguments into a normalized structure before execution.

The default implementation usually:

- receives already-bound `arguments`
- carries over `capabilities`
- adds basic metadata

Override it when:

- the real business arguments are nested inside `tool_call["args"]`, `input["arguments"]`, or another wrapper object
- the public tool entry point is too generic and direct argument binding loses useful detail
- you want to add explicit metadata such as tool source, invocation mode, or tool message id

This determines whether policy logic sees a clear structure like `{command: ...}` or only an opaque generic `input`.

### `normalize_tool_result(...)`

This hook converts tool results or errors into a normalized post-call structure.

The default implementation usually:

- normalizes `result`
- passes through `error`
- adds basic metadata

Override it when:

- the tool returns framework-specific message objects and you want fields like `content`, `artifact`, or `status`
- you want structured error data instead of only a stringified exception
- blocked or sanitized tool results must be adapted into a framework-specific return type

This affects both the `tool_result` event and what after-phase guard logic can evaluate.

## What the normalization return objects look like

The four normalization hooks do not return runtime events directly. They return small dataclasses first, and the patching layer then converts those into actual runtime events.

The mapping is:

- `normalize_llm_input(...)` -> `LLMInputNormalization`
- `normalize_llm_output(...)` -> `LLMOutputNormalization`
- `normalize_tool_invoke(...)` -> `ToolInvokeNormalization`
- `normalize_tool_result(...)` -> `ToolResultNormalization`

A useful mental model is: **the adapter first reshapes framework-native objects into a standard intermediate structure, then AgentGuard turns that intermediate structure into `llm_input`, `llm_output`, `tool_invoke`, and `tool_result` events.**

### `LLMInputNormalization`

It has two fields:

- `payload: Any`
- `metadata: dict[str, Any] = {}`

They mean:

- `payload`: the actual body that will be written into the `llm_input` event
- `metadata`: extra event metadata such as adapter name, label, owner type, and so on

The base implementation usually returns something like:

```python
LLMInputNormalization(
    payload={
        "label": "chat.completions.create",
        "args": [],
        "kwargs": {
            "messages": [{"role": "user", "content": "hello"}],
            "model": "gpt-4o-mini",
        },
    },
    metadata={
        "adapter": "myframework",
        "label": "chat.completions.create",
        "owner_type": "Client",
        "owner_module": "myframework.client",
    },
)
```

That value is then used to build:

- `ev.llm_input(context, normalized.payload, **normalized.metadata)`

So if you want to change what Guard actually sees as the request body, change `payload`. If you want to attach more call-site context, change `metadata`.

### `LLMOutputNormalization`

It also has two fields:

- `payload: Any`
- `metadata: dict[str, Any] = {}`

They mean:

- `payload`: the output content that will be written into the `llm_output` event
- `metadata`: extra output metadata

The base implementation usually returns something like:

```python
LLMOutputNormalization(
    payload={
        "content": "hello back",
    },
    metadata={
        "adapter": "myframework",
        "label": "chat.completions.create",
        "owner_type": "Client",
        "owner_module": "myframework.client",
    },
)
```

If the output is just a plain string, it may also look like:

```python
LLMOutputNormalization(
    payload="hello back",
    metadata={...},
)
```

That value is then used to build:

- `ev.llm_output(context, normalized.payload, **normalized.metadata)`

So the key goal of `LLMOutputNormalization` is to preserve useful structure from complex provider responses instead of collapsing everything into an opaque string.

If your framework can separate hidden reasoning from the final surfaced answer, prefer returning a structured payload such as:

```python
LLMOutputNormalization(
    payload={
        "thought": "intermediate reasoning",
        "final_output": "answer shown to the user",
    },
    metadata={...},
)
```

AgentGuard will keep both semantic fields and also derive `payload.output` for backward-compatible scanning and existing plugin logic.

### `ToolInvokeNormalization`

It has three fields:

- `arguments: dict[str, Any]`
- `capabilities: list[str] | None = None`
- `metadata: dict[str, Any] = {}`

They mean:

- `arguments`: the actual tool arguments that the `tool_invoke` event should expose
- `capabilities`: capability labels such as `shell`, `network`, or `filesystem`
- `metadata`: extra metadata

The base implementation usually returns something like:

```python
ToolInvokeNormalization(
    arguments={
        "command": "rm -rf /tmp/demo",
    },
    capabilities=["shell"],
    metadata={
        "adapter": "langchain",
        "owner_type": "Tool",
        "owner_module": "langchain.tools.base",
    },
)
```

That value is then used to build:

- `ev.tool_invoke(context, tool_name, normalized.arguments, capabilities=normalized.capabilities, **normalized.metadata)`

The most important field here is `arguments`. If this is poorly normalized, plugins may only see a vague generic `input` instead of the real structure such as `{command: ...}`, `{url: ...}`, or `{body: ...}`.

### `ToolResultNormalization`

It has three fields:

- `result: Any`
- `error: str | None = None`
- `metadata: dict[str, Any] = {}`

They mean:

- `result`: the tool return value after execution
- `error`: the error string when execution fails
- `metadata`: extra metadata

The base implementation usually returns something like:

```python
ToolResultNormalization(
    result={
        "stdout": "done",
        "exit_code": 0,
    },
    error=None,
    metadata={
        "adapter": "myframework",
        "owner_type": "ShellTool",
        "owner_module": "myframework.tools",
    },
)
```

If the tool raises, it may instead look like:

```python
ToolResultNormalization(
    result=None,
    error="permission denied",
    metadata={...},
)
```

That value is then used to build:

- `ev.tool_result(context, tool_name, normalized.result, error=normalized.error, **normalized.metadata)`

So `result` controls what after-phase guard logic can inspect, while `error` controls what failure information Guard and trace records can see.

### A simple way to remember them

You can summarize the four dataclasses like this:

- `LLMInputNormalization`: `payload + metadata`
- `LLMOutputNormalization`: `payload + metadata`
- `ToolInvokeNormalization`: `arguments + capabilities + metadata`
- `ToolResultNormalization`: `result + error + metadata`

Where:

- `payload` / `arguments` / `result` are the main event bodies
- `capabilities` is tool-specific risk labeling
- `error` is tool-result-specific failure information
- `metadata` is supplemental context that can travel with every event

## When you should override normalization

A simple rule of thumb is:

- if the default implementation already produces clear, stable, structured event payloads, keep it
- if the default implementation loses critical arguments, collapses everything into strings, or fails to preserve framework semantics, override it

In practice, the most common first overrides are:

- `normalize_tool_invoke(...)`
- `normalize_llm_output(...)`

because many frameworks either wrap tool arguments too aggressively or return overly rich LLM response objects.

## Python example

```python
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter, LLMBinding, ToolBinding
from agentguard.schemas.context import RuntimeContext
from agentguard.utils.errors import AdapterError


class MyAgentAdapter(BaseAgentAdapter):
    name = "myframework"

    def can_wrap(self, agent: Any) -> bool:
        return hasattr(agent, "tools") and hasattr(agent, "model")

    def gettools(self, agent: Any) -> list[ToolBinding]:
        bindings: list[ToolBinding] = []

        tools = getattr(agent, "tools", None)
        if isinstance(tools, list):
            bindings.extend(self.collect_tool_list(tools, func_attrs=("func", "_func")))

        registry = getattr(agent, "function_map", None)
        if isinstance(registry, dict):
            bindings.extend(self.collect_function_map(registry))

        if hasattr(agent, "register_function"):
            bindings.extend(self.collect_register_function(agent))

        return bindings

    def getllm(self, agent: Any) -> list[LLMBinding]:
        model = getattr(agent, "model", None)
        if model is None:
            return []
        return self.collect_llm_methods(model, methods=("create", "invoke", "chat"))

    def generate(
        self,
        agent: Any,
        messages: list[dict[str, Any]],
        context: RuntimeContext,
    ) -> Any:
        _ = context
        fn = getattr(agent, "invoke", None) or getattr(agent, "run", None)
        if callable(fn):
            return fn(messages)
        raise AdapterError("myframework agent exposes no invoke/run")
```

## How to plug it into Guard

### Option 1: use the custom adapter directly

For a project-local integration, instantiate the adapter and call `attach(...)` directly:

```python
from agentguard import Guard

adapter = MyAgentAdapter()
guard = Guard(...)

patched = adapter.attach(agent, guard)
print(patched)
```

### Option 2: add a convenience method on `Guard`

If you want a first-class API like `guard.attach_langchain(agent)`, add a thin wrapper in `agentguard/guard.py`:

```python
def attach_myframework(
    self,
    agent: Any,
    *,
    wrap_tools: bool = True,
    wrap_llm: bool = True,
) -> dict[str, Any]:
    from agentguard.adapters.agent.myframework import MyAgentAdapter

    return MyAgentAdapter().attach(
        agent,
        self,
        wrap_tools=wrap_tools,
        wrap_llm=wrap_llm,
    )
```

Then your application code can simply call:

```python
guard.attach_myframework(agent)
```

## If you want to contribute it as a built-in adapter

If you want to upstream the adapter into this repository, you will usually also update:

1. `src/client/python/agentguard/adapters/agent/myframework.py`
2. `src/client/python/agentguard/adapters/agent/__init__.py`
3. `src/client/python/agentguard/guard.py` with `attach_myframework(...)`
4. `tests/test_attach_adapters.py` with a minimal attach test

## A simple verification checklist

At minimum, verify these three things:

1. `adapter.attach(agent, guard)` returns a sensible patch result
2. tool calls produce `tool_invoke` / `tool_result`
3. model calls produce `llm_input` / `llm_output`

If those three pass, the new adapter is usually wired into AgentGuard runtime enforcement correctly.
