# 自定义框架

如果你的框架还没有内置的 AgentGuard adapter，推荐按照统一的 adapter contract 来实现自定义接入。

## 建议先阅读

先看这份统一约定文档：

- [Agent Adapter 统一约定](adapter_contract.md)

这份文档是 Python 和 JavaScript adapter 的共同规范来源。

## 最小接入步骤

1. 继承 `BaseAgentAdapter`
2. 实现 `can_wrap(...)`
3. 实现 `patchtool(...)`
4. 实现 `patchLLM(...)`
5. 实现 `generate(...)` 作为 best-effort 兜底入口
6. 调用 `attach(...)` 对目标 agent 做原地 patch

## Python 示例

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

## JavaScript 示例

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

## 说明

- 新 adapter 只实现规范名字 `patchtool` 和 `patchLLM`。
- 如果你希望提供像 `guard.attach_myframework(agent)` 这样的快捷 API，可以在 guard 层增加一个薄封装，内部直接调用 `new MyAgentAdapter().attach(agent, guard)`。
