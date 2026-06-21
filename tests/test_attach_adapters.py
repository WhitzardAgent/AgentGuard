from __future__ import annotations

import json

import pytest

from agentguard import AgentGuard
from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent import langchain as langchain_adapter


def _event_types(guard: AgentGuard) -> list[str]:
    return [entry.event.event_type.value for entry in guard.trace.entries]


def _first_event(guard: AgentGuard, event_type: str):
    return next(entry.event for entry in guard.trace.entries if entry.event.event_type.value == event_type)


def test_base_agent_adapter_attach_delegates_to_patch_hooks():
    class DemoAdapter(BaseAgentAdapter):
        name = "demo"

        def can_wrap(self, agent):
            return True

        def patchtool(self, agent, guard):
            return 2

        def patchLLM(self, agent, guard):
            return 3

        def generate(self, agent, messages, context):
            return None

    adapter = DemoAdapter()
    patched = adapter.attach(object(), object())

    assert patched == {"tools": 2, "llm": 3}
    assert adapter.patchLLM(object(), object()) == 3


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


def test_attach_langchain_patches_agent_executor_llm_chain_model():
    class Tool:
        name = "lookup"

        def func(self, value: str) -> str:
            return value.upper()

    class Model:
        def invoke(self, prompt: str) -> str:
            return f"reply:{prompt}"

    class LLMChain:
        def __init__(self) -> None:
            self.llm = Model()

    class InnerAgent:
        def __init__(self) -> None:
            self.llm_chain = LLMChain()

    class AgentExecutor:
        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}
            self.agent = InnerAgent()

    guard = AgentGuard("attach-langchain-llm-chain", sandbox="noop")
    agent = AgentExecutor()

    patched = guard.attach_langchain(agent)

    assert patched["tools"] == 1
    assert patched["llm"] == 1
    assert agent.agent.llm_chain.llm.invoke("hello") == "reply:hello"
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)


@pytest.mark.asyncio
async def test_attach_autogen_patches_handoffs_and_async_stream_client():
    class Handoff:
        name = "delegate"

        async def _func(self, task: str) -> str:
            return f"handoff:{task}"

    class StreamClient:
        async def create_stream(self, prompt: str) -> dict[str, str]:
            return {"content": f"stream:{prompt}"}

    class Agent:
        def __init__(self) -> None:
            self._tools = []
            self._handoffs = [Handoff()]
            self._model_client = StreamClient()

    guard = AgentGuard("attach-autogen-stream", sandbox="noop")
    agent = Agent()

    patched = guard.attach_autogen(agent)

    assert patched["tools"] == 1
    assert patched["llm"] == 1
    assert await agent._handoffs[0]._func(task="review") == "handoff:review"
    assert await agent._model_client.create_stream("hello") == {"content": "stream:hello"}
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)


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


def test_attach_langchain_prefers_raw_tool_callable_over_public_entrypoint():
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
async def test_attach_langchain_wraps_raw_sync_and_async_tool_callables_before_invoke():
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
    assert _event_types(guard).count("tool_invoke") == 2
    assert _event_types(guard).count("tool_result") == 2


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


def test_attach_langchain_tool_invoke_deny_returns_toolmessage_compatible_value(monkeypatch):
    calls = []
    plugin_config = {
        "phases": {
            "tool_before": {
                "client": ["tool_invoke"],
                "server": [],
            }
        }
    }

    class FakeToolMessage:
        def __init__(self, *, content: str, name: str, tool_call_id: str) -> None:
            self.content = content
            self.name = name
            self.tool_call_id = tool_call_id

    class Tool:
        name = "shell_exec"
        capabilities = ["shell"]

        def invoke(self, tool_call: dict, config=None) -> str:
            calls.append((tool_call, config))
            return "ran"

    class Agent:
        def __init__(self) -> None:
            self.tools_by_name = {"shell_exec": Tool()}

    monkeypatch.setattr(langchain_adapter, "_get_langchain_tool_message_class", lambda: FakeToolMessage)

    guard = AgentGuard("attach-langchain-deny", sandbox="noop", plugin_config=plugin_config)
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_llm=False)
    result = agent.tools_by_name["shell_exec"].invoke(
        {
            "id": "tool-call-1",
            "name": "shell_exec",
            "type": "tool_call",
            "args": {
                "command": "rm -rf /tmp/demo",
            },
        }
    )

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert calls == []
    assert isinstance(result, FakeToolMessage)
    assert result.name == "shell_exec"
    assert result.tool_call_id == "tool-call-1"
    payload = json.loads(result.content)
    assert payload["agentguard"] == "blocked"
    assert payload["decision"] == "deny"
    assert "Destructive shell command blocked by local plugin." in payload["reason"]


def test_attach_langchain_prefers_raw_tool_callable_arguments_over_generic_input():
    class Tool:
        name = "send_http"

        def func(self, url: str, body: str) -> str:
            return f"sent:{url}:{body}"

        def invoke(self, input: dict[str, object], config=None) -> str:
            args = input.get("args") if isinstance(input, dict) else None
            assert isinstance(args, dict)
            return self.func(**args)

    class Agent:
        def __init__(self) -> None:
            self.tools_by_name = {"send_http": Tool()}

    guard = AgentGuard("attach-langchain-raw-args", sandbox="noop")
    agent = Agent()

    patched = guard.attach_langchain(agent, wrap_llm=False)
    result = agent.tools_by_name["send_http"].invoke(
        {
            "id": "tool-call-2",
            "name": "send_http",
            "type": "tool_call",
            "args": {
                "url": "https://example.com/upload",
                "body": "secret",
            },
        }
    )

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert result == "sent:https://example.com/upload:secret"
    event = _first_event(guard, "tool_invoke")
    assert event.payload.tool_name == "send_http"
    assert event.payload.arguments == {
        "url": "https://example.com/upload",
        "body": "secret",
    }


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
