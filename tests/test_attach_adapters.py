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
