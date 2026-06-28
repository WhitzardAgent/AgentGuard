# Custom Adapter

如果你的智能体框架还没有内置的 AgentGuard adapter，可以新增一个自定义 adapter，并把它接入到 Guard。

这个章节与 LangChain、LangGraph、LlamaIndex、AutoGen、OpenAI Agents SDK 的接入方式并列，但它面向的是“如何自己实现一个新的 adapter”。

## 这一页在解决什么问题

这里原本有一份单独的 `Agent Adapter Contract` 文档。它本来是在回答一个很具体的问题：**一个 adapter 在 AgentGuard 里到底负责什么，哪些部分必须自己实现，哪些部分可以复用 BaseAgentAdapter。**

为了避免文档拆得太碎，这里直接把这部分内容并入 `Custom Adapter`，先讲清楚 adapter 的职责边界，再进入后面的实现方式、示例代码和接入步骤。

## Adapter 的职责边界

一个 adapter 本身并不负责实现策略判断，它主要做的是把“框架里的调用入口”翻译成 AgentGuard 能统一处理的绑定：

- 找到工具调用入口
- 找到 LLM 调用入口
- 把这些入口描述成 `ToolBinding` / `LLMBinding`
- 交给 `BaseAgentAdapter` 统一完成 patch 和事件接入

因此，一个新的 adapter 通常至少要实现这几个部分：

- `can_wrap(...)`：判断当前 adapter 是否适用于这个 agent
- `gettools(...)`：返回工具调用绑定列表
- `getllm(...)`：返回模型调用绑定列表
- `generate(...)`：提供一个 best-effort 的单轮执行入口

## Base class 会帮你做什么

在 Python 客户端里，自定义 adapter 一般都继承 `BaseAgentAdapter`。

通常你不需要自己重写 `patchtool(...)` 和 `patchLLM(...)`。Base 已经会：

1. 调用 `gettools(...)` / `getllm(...)`
2. 把结果保存到 `self.toolslist` / `self.llms`
3. 对每个 binding 自动完成包装与安装
4. 在调用前后触发统一的 runtime guard event

所以，大多数框架接入时，你真正要写的不是 wrapper 本身，而是“如何把框架原生对象收集成 binding 列表”。

## `ToolBinding` 和 `LLMBinding` 是什么

`gettools(...)` 返回的每一项都是一个 `ToolBinding`。它最核心的三个字段是：

- 工具名 `name`
- 参数说明 `parameters`
- 真实调用函数 `callable`

另外还可以附带：

- `owner` / `attr`：这个 callable 挂在哪个对象属性上
- `container` / `key`：这个 callable 是否存放在某个 `list` / `dict` 里
- `tool` / `capabilities`：补充工具元信息
- `installer`：默认安装逻辑不够时，用来自定义 patch 安装方式

`getllm(...)` 返回 `LLMBinding` 列表，核心字段是：

- `label`
- `callable`

并且同样可以带上：

- `owner` / `attr`
- `container` / `key`
- `installer`

## 最小实现步骤

1. 继承 `BaseAgentAdapter`
2. 实现 `can_wrap(...)`
3. 实现 `gettools(...)`
4. 实现 `getllm(...)`
5. 实现 `generate(...)`
6. 如果框架对象结构特殊，再按需重写 normalization
7. 用 `adapter.attach(agent, guard)` 或在 `Guard` 上增加一个快捷方法

## 如何实现 `can_wrap(...)`

`can_wrap(...)` 的目标不是“尽可能猜到这个 agent 能不能跑”，而是**可靠地判断当前 adapter 是否就是这个对象应该使用的 adapter**。

比较推荐的判断方式有：

- 看 `type(agent).__module__` 是否包含框架特征路径
- 看 agent 上是否存在一组稳定的关键属性
- 同时结合类型来源和对象结构做双重判断

例如：

```python
def can_wrap(self, agent: Any) -> bool:
    mod = type(agent).__module__ or ""
    return "myframework" in mod and hasattr(agent, "tools") and hasattr(agent, "model")
```

实现时建议注意：

- 尽量保守，不要为了“多匹配一点对象”而误伤别的框架
- 优先用稳定特征，不要依赖容易变化的临时属性
- 如果两个 adapter 可能匹配同一类对象，`can_wrap(...)` 应尽量做得更具体
- 如果你的项目是纯自定义对象，没有明显模块名，也可以只靠结构判断

可以把它理解成 adapter 的“识别器”。识别越准，后续 patch 越稳定。

## 如何实现 `gettools(...)`

`gettools(...)` 负责把框架里的工具入口收集成 `list[ToolBinding]`。

它要解决的核心问题是：**这个框架里，真正会执行工具逻辑的函数到底在哪里。**

常见来源包括：

- `agent.tools` 这样的工具列表
- `agent.tools_by_name` 这样的名字到工具对象映射
- `function_map` 这样的函数注册表
- `register_function(...)` 这样的延迟注册入口
- 工具对象上的 `func`、`_func`、`run_json`、`invoke`、`_run`、`coroutine` 等真实执行函数

最简单的情况通常是直接复用 Base 的 helper：

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

写 `gettools(...)` 时最关键的是选对“patch 点”：

- 如果工具对象同时有 `invoke(...)` 和底层 `func(...)`，通常优先 patch 更接近真实业务参数的那个
- 如果 public entrypoint 会把参数包成通用 `input`，而底层函数仍保留结构化参数，优先 patch 底层函数通常更利于风险判断
- 如果只有 public entrypoint 可用，那就 patch 它，并在 normalization 里把参数重新展开
- 如果框架会在运行中动态注册工具，除了收集已有工具，通常还要 patch 注册入口

你返回的每个 `ToolBinding` 至少应回答三个问题：

- 这个工具叫什么
- 它的真实 callable 是哪个
- patch 之后应该把 wrapper 安装回哪里

如果默认安装逻辑不够，还可以在 binding 上提供 `installer`，用来自定义如何把 wrapper 装回框架对象。

## 如何实现 `getllm(...)`

`getllm(...)` 负责把框架里的模型调用入口收集成 `list[LLMBinding]`。

这里的重点是：**找到真正发起模型请求的 callable，而不是只找到一个更高层的业务包装器。**

常见入口包括：

- `model.invoke(...)`
- `client.create(...)`
- `chat.completions.create(...)`
- `messages.create(...)`
- `create_stream(...)`
- 某些框架里的 `_client.xxx` 深层调用路径

如果目标对象和方法路径比较清晰，可以直接复用 `collect_llm_methods(...)`：

```python
def getllm(self, agent: Any) -> list[LLMBinding]:
    model = getattr(agent, "model", None)
    if model is None:
        return []
    return self.collect_llm_methods(model, methods=("create", "invoke", "chat"))
```

`label` 的作用可以理解成“这个 LLM 入口在事件里的标识名”。它通常会出现在：

- `llm_input` 的 payload 或 metadata
- trace 中的调用来源说明
- 调试时区分不同模型入口

实现时建议注意：

- 如果同一个框架有多层包装，尽量避免把同一轮请求重复 patch 出多份 `llm_input` / `llm_output`
- 如果框架既支持同步又支持异步或流式接口，可以分别把不同入口都收集出来
- 如果不同模型 provider 的调用路径不同，可以像 AutoGen 那样按 client 类型分支选择 methods

一句话说，`getllm(...)` 要做的是把“模型真正被调用的那个点”暴露给 AgentGuard。

## 不同 normalization 是做什么的

normalization 的作用，是把不同框架里形状各异的原始对象，转换成 AgentGuard runtime event 能稳定消费的统一结构。

Base class 已经提供了一套最基础的默认实现；如果你的框架对象本身就比较简单，很多时候完全不需要重写。

但当框架把参数、消息、结果包装得比较深时，你通常就需要自定义 normalization。

### `normalize_llm_input(...)`

它负责把一次 LLM 调用前的输入转换成统一 payload。

默认实现大致会保留：

- `label`
- `args`
- `kwargs`
- 一些基础 metadata

适合重写的情况：

- 消息真正藏在 `kwargs["messages"]`、`kwargs["input"]` 或框架私有对象里
- 你希望把 message object 展开成更稳定的 `{role, content}` 结构
- 你希望补充 model 名、provider 名、owner 类型等额外 metadata

它最终影响的是 `llm_input` 事件里“Guard 实际看到的请求内容”。

### `normalize_llm_output(...)`

它负责把一次 LLM 返回值转换成统一输出结构。

默认实现通常会：

- 尝试把对象转成基础类型、`dict` 或字符串
- 记录输出的基础 metadata

适合重写的情况：

- 框架返回的是复杂响应对象，不重写就只能得到一段 `str(...)`
- 你希望保留 `content`、`tool_calls`、`response_metadata`、`usage` 之类的结构化字段
- 你希望在 event 里明确区分文本输出、消息对象输出、流式块输出

它最终影响的是 `llm_output` 事件的内容质量。

### `normalize_tool_invoke(...)`

它负责把工具调用前的参数转换成统一结构。

默认实现通常会：

- 接收已经绑定好的 `arguments`
- 写入 `capabilities`
- 补充一些基础 metadata

适合重写的情况：

- 框架把真实参数包在 `tool_call["args"]`、`input["arguments"]` 等嵌套结构里
- public tool entrypoint 的签名过于通用，直接绑定参数拿不到真正业务字段
- 你希望显式补充工具来源、调用模式、tool message id 等信息

它最终影响的是 `tool_invoke` 事件里，策略插件到底是看到 `{command: ...}`，还是只看到一个模糊的 `input`。

### `normalize_tool_result(...)`

它负责把工具执行后的结果或异常转换成统一结构。

默认实现通常会：

- 规范化 `result`
- 透传 `error`
- 补充一些基础 metadata

适合重写的情况：

- 工具返回的是框架专用消息对象，需要提取 `content`、`artifact`、`status` 等字段
- 你希望把异常进一步结构化，而不是只记录一段错误字符串
- 工具被 block 后，需要把结果适配成框架要求的返回对象

它最终影响的是 `tool_result` 事件以及 after-phase 风险判断能看到的结果内容。

## normalization 的返回对象长什么样

四个 normalization hook 都不是直接返回 event；它们返回的是几个轻量的 dataclass，随后再由 patching 层转换成真正的 runtime event。

对应关系是：

- `normalize_llm_input(...)` -> `LLMInputNormalization`
- `normalize_llm_output(...)` -> `LLMOutputNormalization`
- `normalize_tool_invoke(...)` -> `ToolInvokeNormalization`
- `normalize_tool_result(...)` -> `ToolResultNormalization`

可以把它理解成：**adapter 先把框架对象整理成标准中间结构，AgentGuard 再把这个中间结构组装成 `llm_input` / `llm_output` / `tool_invoke` / `tool_result` event。**

### `LLMInputNormalization`

定义上它有两个字段：

- `payload: Any`
- `metadata: dict[str, Any] = {}`

含义分别是：

- `payload`：真正要写入 `llm_input` event 的主体内容
- `metadata`：附加到 event metadata 上的额外信息，比如 adapter 名、label、owner 类型等

Base 默认实现大致会返回：

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

后续它会被用来构造：

- `ev.llm_input(context, normalized.payload, **normalized.metadata)`

所以如果你想影响 `llm_input` 事件里“Guard 实际看到什么请求内容”，主要改的是 `payload`；如果你想补充调用来源信息，主要改的是 `metadata`。

### `LLMOutputNormalization`

定义上也有两个字段：

- `payload: Any`
- `metadata: dict[str, Any] = {}`

含义分别是：

- `payload`：真正要写入 `llm_output` event 的输出内容
- `metadata`：附加的输出元信息

Base 默认实现大致会返回：

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

如果输出是普通字符串，也可能是：

```python
LLMOutputNormalization(
    payload="hello back",
    metadata={...},
)
```

后续它会被用来构造：

- `ev.llm_output(context, normalized.payload, **normalized.metadata)`

所以 `LLMOutputNormalization` 的关键是：尽量把复杂 provider response 转成对策略和审计更有价值的结构，而不是退化成一段难以分析的字符串。

如果你的框架能把隐藏推理和最终可见回答区分开，推荐返回这种结构化 payload：

```python
LLMOutputNormalization(
    payload={
        "thought": "intermediate reasoning",
        "final_output": "answer shown to the user",
    },
    metadata={...},
)
```

AgentGuard 会同时保留这两个语义字段，并额外推导出 `payload.output`，用于兼容旧扫描逻辑和现有 plugin。

### `ToolInvokeNormalization`

它有三个字段：

- `arguments: dict[str, Any}`
- `capabilities: list[str] | None = None`
- `metadata: dict[str, Any] = {}`

含义分别是：

- `arguments`：真正要让 `tool_invoke` event 看到的工具参数
- `capabilities`：这个工具的能力标签，比如 `shell`、`network`、`filesystem`
- `metadata`：额外元信息

Base 默认实现大致会返回：

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

后续它会被用来构造：

- `ev.tool_invoke(context, tool_name, normalized.arguments, capabilities=normalized.capabilities, **normalized.metadata)`

这里最重要的是 `arguments`。如果这个字段整理得不好，插件看到的就可能只是一个模糊的 `input`，而不是真实的 `{command: ...}`、`{url: ...}`、`{body: ...}`。

### `ToolResultNormalization`

它有三个字段：

- `result: Any`
- `error: str | None = None`
- `metadata: dict[str, Any] = {}`

含义分别是：

- `result`：工具执行后的返回值
- `error`：如果执行失败，对应的错误字符串
- `metadata`：额外元信息

Base 默认实现大致会返回：

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

如果工具抛错，则可能是：

```python
ToolResultNormalization(
    result=None,
    error="permission denied",
    metadata={...},
)
```

后续它会被用来构造：

- `ev.tool_result(context, tool_name, normalized.result, error=normalized.error, **normalized.metadata)`

因此，`result` 决定 after-phase 能看到什么结果内容，`error` 决定失败场景下 Guard 和 trace 能拿到什么异常信息。

### 一个简单的理解方式

这四个 dataclass 可以按下面方式记：

- `LLMInputNormalization`：`payload + metadata`
- `LLMOutputNormalization`：`payload + metadata`
- `ToolInvokeNormalization`：`arguments + capabilities + metadata`
- `ToolResultNormalization`：`result + error + metadata`

其中：

- `payload` / `arguments` / `result` 是“事件主体”
- `capabilities` 是 tool 专有的风险能力标签
- `error` 是 tool result 专有的错误信息
- `metadata` 是所有 event 都可以附带的补充上下文

## 什么时候应该重写 normalization

可以用一个很简单的判断标准：

- 如果默认实现已经能把事件变成清晰、稳定、结构化的 payload，就不用重写
- 如果默认实现只能拿到模糊字符串、丢了关键参数，或者无法还原框架语义，就应该重写

通常最常见的是先重写：

- `normalize_tool_invoke(...)`
- `normalize_llm_output(...)`

因为很多框架的问题恰好出在“工具参数被包起来了”或者“模型输出对象太复杂了”。

## Python 示例

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

## 如何接入 Guard

### 方式 1：直接使用自定义 adapter

如果只是项目内一次性使用，最简单的方式是直接实例化 adapter 并调用 `attach(...)`：

```python
from agentguard import Guard

adapter = MyAgentAdapter()
guard = Guard(...)

patched = adapter.attach(agent, guard)
print(patched)
```

### 方式 2：把它变成 Guard 的快捷方法

如果你希望像 `guard.attach_langchain(agent)` 一样使用，可以在 `agentguard/guard.py` 里增加一个薄封装：

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

然后业务代码里就可以直接调用：

```python
guard.attach_myframework(agent)
```

## 如果要作为内置 adapter 提交

如果你想把它作为内置 adapter 贡献到仓库里，通常还需要同步更新：

1. `src/client/python/agentguard/adapters/agent/myframework.py`
2. `src/client/python/agentguard/adapters/agent/__init__.py`
3. `src/client/python/agentguard/guard.py` 里的 `attach_myframework(...)`
4. `tests/test_attach_adapters.py` 里的最小 attach 测试

## 一个简单的验证清单

最少确认三件事：

1. `adapter.attach(agent, guard)` 返回合理的 patch 结果
2. tool 调用能产出 `tool_invoke` / `tool_result`
3. model 调用能产出 `llm_input` / `llm_output`

通常这三点通过，就说明新的 adapter 已经真正接入了 AgentGuard 的运行时防护链路。
