# Agent Adapter 统一约定

这份文档定义了 AgentGuard Python 和 JavaScript 客户端共用的 adapter 抽象约定。

## 目标

agent adapter 的职责，是在不改动框架原生执行循环的前提下，对框架对象做原地补丁。

Python 和 JS 两端在概念上保持一致：

- `attach(...)` 是统一入口
- `patchtool(...)` 负责补丁工具调用点
- `patchLLM(...)` 负责补丁模型调用点
- `generate(...)` 是面向测试和兜底执行的 best-effort 辅助方法

## 必须实现或遵守的 hook

### `attach(agent, guard, *, wrap_tools=True, wrap_llm=True)` / `attach(agent, guard, { wrap_tools = true, wrap_llm = true })`

职责：

- 原地 patch 目标对象
- 根据开关分别处理 tools 和 llm
- 返回 `{ "tools": int, "llm": int }` 形式的补丁计数

规范：

- `attach(...)` 内不要真正运行 agent
- patch 过程中不要主动执行工具或模型调用
- 应尽量保证幂等性；已经包裹过的调用点应跳过

### `patchtool(agent, guard)`

职责：

- 找到框架中的工具容器
- 用 AgentGuard 包裹真实的工具调用入口
- 保持原有参数传递方式和对象绑定关系
- 返回成功 patch 的工具调用点数量

典型 patch 目标包括：

- tool 列表
- tool registry / map
- tool node 容器
- 运行时新增工具的注册 API

### `patchLLM(agent, guard)`

职责：

- 找到框架中的模型对象或底层 client
- 包裹真实发生 LLM 调用的方法
- 返回成功 patch 的 LLM 调用点数量

典型 patch 目标包括：

- 直接模型对象，如 `agent.model`
- 嵌套 client，如 `agent._model_client`
- provider SDK 暴露出的 completion / response namespace

### `can_wrap(agent)`

职责：

- 判断当前 adapter 是否适用于该框架对象
- 检测逻辑要轻量，不应修改对象状态

### `generate(agent, messages, context)`

职责：

- 提供单轮 best-effort 执行入口
- 主要用于测试或兜底执行路径

规范：

- 不要无必要地重复实现框架完整编排逻辑
- 如果没有可运行入口，要抛出清晰的 adapter error

## 规范命名

新代码统一使用以下 hook 名：

- `patchtool`
- `patchLLM`

现在只保留以下规范 hook 名：

- `patchtool`
- `patchLLM`

## 实现规范

每个 adapter 都应遵守以下规则：

- 只 patch 当前框架接入层真正拥有的调用点
- patch 范围尽量局部、可理解
- 已经 guarded 的 callable 不要重复包裹
- 保持同步 / 异步语义不变
- 保持 `self` / `this` 绑定关系不变
- 只统计真正成功的 patch 次数
- 遇到部分内部结构不存在时应安全返回 `0`

## Python 骨架

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

## JavaScript 骨架

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

## 如何使用自定义 adapter

如果只是一次性接入，可以直接实例化 adapter 并调用 `attach(...)`。

Python：

```python
adapter = MyAgentAdapter()
patched = adapter.attach(agent, guard)
```

JavaScript：

```js
const adapter = new MyAgentAdapter();
const patched = adapter.attach(agent, guard);
```

如果你希望像 `guard.attach_langchain(agent)` 这样提供一层框架专用快捷方法，可以在 guard 层增加一个薄封装，内部直接委托给 `new MyAgentAdapter().attach(...)`。
