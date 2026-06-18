# Agent Adapter Contract

This page defines the shared adapter contract used by the Python and JavaScript AgentGuard clients.

## Goal

An agent adapter patches a framework object in place, while keeping the framework's native execution loop unchanged.

The adapter contract is identical across Python and JS at the conceptual level:

- `attach(...)` is the entry point.
- `patchtool(...)` patches tool call sites.
- `patchLLM(...)` patches model call sites.
- `generate(...)` is a best-effort helper for direct invocation flows and tests.

## Required hooks

### `attach(agent, guard, *, wrap_tools=True, wrap_llm=True)` / `attach(agent, guard, { wrap_tools = true, wrap_llm = true })`

Responsibilities:

- patch the target object in place
- selectively patch tools and/or LLMs based on flags
- return patch counts in `{ "tools": int, "llm": int }`

Rules:

- do not run the agent inside `attach(...)`
- do not execute tools or model calls during patching
- prefer idempotent patching; already wrapped call sites should be skipped

### `patchtool(agent, guard)`

Responsibilities:

- locate the framework's tool containers
- wrap each concrete tool entry point with AgentGuard
- preserve native argument passing and object binding
- return the number of tool call sites patched

Typical targets include:

- tool lists
- tool registries / maps
- tool-node containers
- registration APIs that accept new tools after startup

### `patchLLM(agent, guard)`

Responsibilities:

- locate the framework's model or client object
- wrap the framework's real LLM invocation methods
- return the number of LLM call sites patched

Typical targets include:

- direct model objects such as `agent.model`
- nested clients such as `agent._model_client`
- completion / response namespaces exposed by provider SDKs

### `can_wrap(agent)`

Responsibilities:

- identify whether this adapter matches the incoming framework object
- stay lightweight; detection should not mutate the object

### `generate(agent, messages, context)`

Responsibilities:

- provide a best-effort single-turn execution path
- support tests and fallback execution helpers

Rules:

- do not duplicate framework orchestration logic unless needed
- raise a clear adapter error when no runnable path exists

## Canonical names

For new code, implement these hook names in both Python and JS:

- `patchtool`
- `patchLLM`

Only these canonical hook names are supported:

- `patchtool`
- `patchLLM`

## Implementation rules

Every adapter should follow these rules:

- patch only call sites owned by the target framework integration
- keep patching local and reversible in principle
- never double-wrap an already guarded callable
- preserve sync vs async behavior
- preserve `self` / `this` binding for bound methods
- count only successful patch operations
- tolerate partially missing framework internals and return `0` when nothing matches

## Python skeleton

```python
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.schemas.context import RuntimeContext


class MyAgentAdapter(BaseAgentAdapter):
    name = "myframework"

    def can_wrap(self, agent: Any) -> bool:
        return hasattr(agent, "tools") and hasattr(agent, "model")

    def patchtool(self, agent: Any, guard: Any) -> int:
        patched = 0
        tools = getattr(agent, "tools", None)
        if isinstance(tools, list):
            for index, tool in enumerate(tools):
                ...
                patched += 1
        return patched

    def patchLLM(self, agent: Any, guard: Any) -> int:
        model = getattr(agent, "model", None)
        if model is None:
            return 0
        ...
        return 1

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        return agent.invoke(messages)
```

## JavaScript skeleton

```js
const { BaseAgentAdapter } = require("./base");

class MyAgentAdapter extends BaseAgentAdapter {
  constructor() {
    super();
    this.name = "myframework";
  }

  can_wrap(agent) {
    return Boolean(agent && agent.tools && agent.model);
  }

  patchtool(agent, guard) {
    let patched = 0;
    const tools = agent && agent.tools;
    if (Array.isArray(tools)) {
      for (const tool of tools) {
        ...
        patched += 1;
      }
    }
    return patched;
  }

  patchLLM(agent, guard) {
    const model = agent && agent.model;
    if (!model) {
      return 0;
    }
    ...
    return 1;
  }

  async generate(agent, messages) {
    return agent.invoke(messages);
  }
}
```

## Using a custom adapter

For one-off integrations, instantiate the adapter directly and call `attach(...)`.

Python:

```python
adapter = MyAgentAdapter()
patched = adapter.attach(agent, guard)
```

JavaScript:

```js
const adapter = new MyAgentAdapter();
const patched = adapter.attach(agent, guard);
```

If you want a first-class helper like `guard.attach_langchain(agent)`, add a thin wrapper in the guard layer that delegates to `new MyAgentAdapter().attach(...)`.
