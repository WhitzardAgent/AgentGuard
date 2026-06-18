# Custom Framework

If your framework is not covered by a built-in AgentGuard adapter, implement a custom adapter against the shared adapter contract.

## Recommended reading

Read the shared contract first:

- [Agent Adapter Contract](adapter_contract.md)

That contract is the source of truth for both Python and JavaScript adapters.

## Minimal workflow

1. inherit `BaseAgentAdapter`
2. implement `can_wrap(...)`
3. implement `patchtool(...)`
4. implement `patchLLM(...)`
5. implement `generate(...)` as a best-effort fallback
6. call `attach(...)` to patch the target agent in place

## Python example

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
            for tool in tools:
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


adapter = MyAgentAdapter()
patched = adapter.attach(agent, guard)
print(patched)
```

## JavaScript example

```js
const { BaseAgentAdapter } = require("./adapters/agent/base");

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

const adapter = new MyAgentAdapter();
const patched = adapter.attach(agent, guard);
console.log(patched);
```

## Notes

- Implement only the canonical hooks `patchtool` and `patchLLM`.
- If you need a convenience API such as `guard.attach_myframework(agent)`, add a thin wrapper around `new MyAgentAdapter().attach(agent, guard)`.
