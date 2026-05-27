# 自定义框架

我们后续会积极适配主流的智能体开发框架，提供可直接使用的 Adapter。但是若你使用的智能体不是用主流智能体框架开发的，或是开发框架尚未得到我们的适配，下面将给你一份操作指南，指导你如何自己编写定制化的 Adapter。

## 第 1 步：继承 `BaseAdapter` 并实现 `install` 方法
首先，你需要在 `agentguard/sdk/adapters/` 目录下创建一个 `py` 文件，在该文件中创建一个继承 `BaseAdapter` 的类，这里我们以 `MyAdapter` 为例。

我们要在 `MyAdapter` 类中实现 `install` 方法。

```python
from agentguard.sdk.adapters.base import BaseAdapter

class MyAdapter(BaseAdapter):

    def install(self, agent):
        ...
```

`install()` 的输入参数是一个智能体实例，它依赖于你使用的智能体本身的实现。具体选择哪种智能体实例，由你自己决定，但一个基本原则是，你需要有条件从该实例中获取到智能体的所有工具的元数据，即工具的名称以及工具的函数实现，工具的函数实现中一般会包含参数的签名。

## 第 2 步：从智能体实例中获取工具的元数据
我们无法具体说明如何从智能体实例中获取工具的元数据，因为这依赖于你使用的智能体本身的实现。你可以参考我们对 LangChain, AutoGen 和 OpenAI Agents SDK 的处理：

* `agentguard/sdk/adapters/langchain.py`
* `agentguard/sdk/adapters/autogen.py`
* `agentguard/sdk/adapters/openai_agents.py`

## 第 3 步：使用 `wrap_tool` 绑定工具
当你获得了工具名和对应的工具函数实现后，你可以使用 `wrap_tool(self.guard, tool_name, tool_function)` 方法将 AgentGuard 客户端绑定到工具中。

代码示例如下：

```python
from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.wrappers import wrap_tool

class MyAdapter(BaseAdapter):

    def install(self, agent):
        ...
        # Assume you have obtained the

        # tools_metadata = {
        #   "<tool_name>": <tool_function>,
        #   ...
        # }

        # from the agent instance.
        for tool_name, tool_function in tools_metadata.items():
            wrap_tool(self.guard, tool_name, tool_function)
```

## 第 4 步：在智能体中使用自定义的 Adapter
你可以使用 `guard.attach_custom_agents()` 来调用自定义的 Adapter。

```python
agent = ...

guard = Guard(...)
guard.start(...)
guard.attach_custom_agents(agent, MyAdapter)
```