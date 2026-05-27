# Custom Framework

We are actively working on adapters for mainstream agent frameworks. But if your agent isn't built with a supported framework — or your framework hasn't been adapted yet — this guide will walk you through writing a custom adapter.

## Step 1: Inherit `BaseAdapter` and implement `install`

Create a Python file under `agentguard/sdk/adapters/` and define a class that inherits `BaseAdapter`. Here we use `MyAdapter` as an example.

You need to implement the `install` method in your adapter class.

```python
from agentguard.sdk.adapters.base import BaseAdapter

class MyAdapter(BaseAdapter):

    def install(self, agent):
        ...
```

The `install()` method takes an agent instance as input. The choice of which instance to pass depends on your framework's implementation, but a key requirement is that you must be able to extract all tool metadata — tool names and function implementations (which typically include parameter signatures) — from that instance.

## Step 2: Extract tool metadata from the agent instance

The exact method for extracting tool metadata depends on your framework. You can reference our existing adapters for LangChain, AutoGen, and OpenAI Agents SDK:

* `agentguard/sdk/adapters/langchain.py`
* `agentguard/sdk/adapters/autogen.py`
* `agentguard/sdk/adapters/openai_agents.py`

## Step 3: Bind tools with `wrap_tool`

Once you have the tool names and their function implementations, use `wrap_tool(self.guard, tool_name, tool_function)` to bind each tool to the AgentGuard client.

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

## Step 4: Use the custom adapter in your agent

Call `guard.attach_custom_agents()` to activate your custom adapter.

```python
agent = ...

guard = Guard(...)
guard.start(...)
guard.attach_custom_agents(agent, MyAdapter)
```