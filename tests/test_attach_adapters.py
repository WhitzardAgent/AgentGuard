from __future__ import annotations

import json

import pytest

from agentguard import AgentGuard


def _event_types(guard: AgentGuard) -> list[str]:
    return [entry.event.event_type.value for entry in guard.trace.entries]


def _first_event(guard: AgentGuard, event_type: str):
    return next(entry.event for entry in guard.trace.entries if entry.event.event_type.value == event_type)


def test_wrap_agent_is_not_exposed():
    guard = AgentGuard("wrap-disabled", sandbox="noop")
    assert not hasattr(guard, "wrap_agent")


def test_attach_autogen_patches_tool_and_llm_method():
    calls = []

    def search(query: str) -> str:
        calls.append(query)
        return f"found:{query}"

    class Tool:
        name = "search"
        _func = staticmethod(search)

    class ModelClient:
        def create(self, prompt: str) -> str:
            return f"model:{prompt}"

    class Agent:
        def __init__(self) -> None:
            self._tools = [Tool()]
            self._model_client = ModelClient()

    guard = AgentGuard("attach-autogen", sandbox="noop")
    agent = Agent()

    patched = guard.attach_autogen(agent)

    assert patched["tools"] == 1
    assert patched["llm"] == 1
    assert agent._tools[0]._func(query="abc") == "found:abc"
    assert agent._model_client.create("hello") == "model:hello"
    assert calls == ["abc"]
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)
    assert _first_event(guard, "llm_output").metadata["output_type"] == "str"


def test_attach_langchain_patches_tools_by_name():
    def lookup(value: str) -> str:
        return value.upper()

    class Tool:
        name = "lookup"
        func = staticmethod(lookup)

    class Model:
        def invoke(self, prompt: str) -> str:
            return f"reply:{prompt}"

    class Agent:
        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}
            self.model = Model()

    guard = AgentGuard("attach-langchain", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent)

    assert patched["tools"] == 1
    assert patched["llm"] == 1
    assert agent.tools_by_name["lookup"].func(value="abc") == "ABC"
    assert agent.model.invoke("hello") == "reply:hello"
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)
    assert _first_event(guard, "llm_output").metadata["output_type"] == "str"


def test_attach_langchain_nested_llm_wrappers_emit_one_pair():
    class Client:
        def create(self, prompt: str) -> dict[str, str]:
            return {"content": f"reply:{prompt}"}

    class Model:
        __module__ = "langchain_openai.chat_models.base"

        def __init__(self) -> None:
            self.client = Client()

        def invoke(self, prompt: str) -> dict[str, str]:
            return self.client.create(prompt)

    class Agent:
        __module__ = "langchain.agents.factory"

        def __init__(self) -> None:
            model = Model()

            def capture_model():
                return model

            class Runnable:
                def __init__(self) -> None:
                    self.func = capture_model

            class Node:
                def __init__(self, runnable) -> None:
                    self.runnable = runnable

            self.nodes = {"model": Node(Runnable())}

    guard = AgentGuard("attach-langchain-nested-llm", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_tools=False)
    response = agent.nodes["model"].runnable.func().invoke("hello")

    assert patched["tools"] == 0
    assert patched["llm"] == 1
    assert response == {"content": "reply:hello"}
    assert _event_types(guard).count("llm_input") == 1
    assert _event_types(guard).count("llm_output") == 1


def test_attach_langchain_patches_toolnode_bound_tools_by_name():
    def lookup(value: str) -> str:
        return value.upper()

    class Tool:
        name = "lookup"
        func = staticmethod(lookup)

    class ToolNode:
        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}

    class Node:
        def __init__(self) -> None:
            self.bound = ToolNode()

    class Model:
        def invoke(self, prompt: str) -> str:
            return f"reply:{prompt}"

    class Agent:
        def __init__(self) -> None:
            self.nodes = {"tools": Node()}
            self.model = Model()

    guard = AgentGuard("attach-langchain-toolnode", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_llm=False)

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert agent.nodes["tools"].bound.tools_by_name["lookup"].func(value="abc") == "ABC"
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)


def test_attach_langchain_prefers_public_tool_entrypoint():
    calls = []

    class Tool:
        name = "lookup"

        def func(self, value: str) -> str:
            calls.append(("func", value))
            return value.upper()

        def _run(self, value: str) -> str:
            calls.append(("_run", value))
            return self.func(value)

        def invoke(self, value: str) -> str:
            calls.append(("invoke", value))
            return self._run(value)

    class Agent:
        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}

    guard = AgentGuard("attach-langchain-nested-tool", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_llm=False)
    result = agent.tools_by_name["lookup"].invoke(value="abc")

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert result == "ABC"
    assert calls == [("invoke", "abc"), ("_run", "abc"), ("func", "abc")]
    assert _event_types(guard).count("tool_invoke") == 1
    assert _event_types(guard).count("tool_result") == 1


@pytest.mark.asyncio
async def test_attach_langchain_falls_back_to_internal_tool_methods():
    calls = []

    class Tool:
        name = "lookup"

        def func(self, value: str) -> str:
            calls.append(("func", value))
            return value.upper()

        async def coroutine(self, value: str) -> str:
            calls.append(("coroutine", value))
            return value.lower()

        def invoke(self, value: str) -> str:
            calls.append(("invoke", value))
            return f"invoke:{value}"

    class Agent:
        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}

    guard = AgentGuard("attach-langchain-prefer-func", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_llm=False)
    tool = agent.tools_by_name["lookup"]

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert tool.invoke(value="ABC") == "invoke:ABC"
    assert tool.func(value="ABC") == "ABC"
    assert await tool.coroutine(value="ABC") == "abc"
    assert calls == [("invoke", "ABC"), ("func", "ABC"), ("coroutine", "ABC")]
    assert _event_types(guard).count("tool_invoke") == 1
    assert _event_types(guard).count("tool_result") == 1


@pytest.mark.asyncio
async def test_attach_langchain_patches_internal_tool_methods_when_public_entrypoint_missing():
    calls = []

    class Tool:
        name = "lookup"

        def func(self, value: str) -> str:
            calls.append(("func", value))
            return value.upper()

        async def coroutine(self, value: str) -> str:
            calls.append(("coroutine", value))
            return value.lower()

    class Agent:
        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}

    guard = AgentGuard("attach-langchain-fallback-tool", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_llm=False)
    tool = agent.tools_by_name["lookup"]

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert tool.func(value="ABC") == "ABC"
    assert await tool.coroutine(value="ABC") == "abc"
    assert calls == [("func", "ABC"), ("coroutine", "ABC")]
    assert _event_types(guard).count("tool_invoke") == 2
    assert _event_types(guard).count("tool_result") == 2


@pytest.mark.asyncio
async def test_attach_openai_agents_patches_async_on_invoke_tool():
    class FunctionTool:
        name = "send"

        async def on_invoke_tool(self, run_context, json_input: str) -> str:
            args = json.loads(json_input)
            return f"sent:{args['message']}"

    class Completions:
        def create(self, **kwargs):
            return {"choices": [{"message": {"content": kwargs["messages"][0]["content"]}}]}

    class Chat:
        def __init__(self) -> None:
            self.completions = Completions()

    class Client:
        def __init__(self) -> None:
            self.chat = Chat()

    class Agent:
        def __init__(self) -> None:
            self.tools = [FunctionTool()]
            self.client = Client()

    guard = AgentGuard("attach-openai", sandbox="noop")
    agent = Agent()

    patched = guard.attach_openai_agents(agent)
    result = await agent.tools[0].on_invoke_tool(None, '{"message": "hello"}')
    response = agent.client.chat.completions.create(messages=[{"content": "hi"}])

    assert patched["tools"] == 1
    assert patched["llm"] == 1
    assert result == "sent:hello"
    assert response["choices"][0]["message"]["content"] == "hi"
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)
    assert _first_event(guard, "llm_output").metadata["output_type"] == "dict"
