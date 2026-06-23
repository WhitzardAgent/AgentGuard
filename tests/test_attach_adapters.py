from __future__ import annotations

import json
import types

import pytest

from agentguard import AgentGuard
from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent import langchain as langchain_adapter
from agentguard.adapters.agent import langgraph as langgraph_adapter


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


def test_agentguard_exposes_attach_langgraph():
    guard = AgentGuard("attach-langgraph-api", sandbox="noop")

    assert hasattr(guard, "attach_langgraph")


def test_attach_langgraph_patches_compiled_graph_toolnode_and_static_model():
    class Tool:
        name = "lookup"

        def func(self, value: str) -> str:
            return value.upper()

    class ToolNode:
        __module__ = "langgraph.prebuilt.tool_node"

        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}

    class Model:
        def invoke(self, state, config=None):
            return {"messages": [f"reply:{state}"]}

    class RunnableCallable:
        __module__ = "langgraph._internal._runnable"

        def __init__(self) -> None:
            static_model = Model()

            def call_model(state, config=None):
                return static_model.invoke(state, config)

            self.func = call_model

    class PregelNode:
        __module__ = "langgraph.pregel._read"

        def __init__(self, bound) -> None:
            self.bound = bound

    class CompiledGraph:
        __module__ = "langgraph.graph.state"

        def __init__(self) -> None:
            self.nodes = {
                "tools": PregelNode(ToolNode()),
                "agent": PregelNode(RunnableCallable()),
            }

        def invoke(self, state):
            return self.nodes["agent"].bound.func(state)

    guard = AgentGuard("attach-langgraph-compiled", sandbox="noop")
    graph = CompiledGraph()

    patched = guard.attach_langgraph(graph)
    tool_result = graph.nodes["tools"].bound.tools_by_name["lookup"].func(value="abc")
    llm_result = graph.invoke({"messages": [{"role": "user", "content": "hi"}]})

    assert patched == {"tools": 1, "llm": 1}
    assert tool_result == "ABC"
    assert llm_result == {"messages": ["reply:{'messages': [{'role': 'user', 'content': 'hi'}]}"]}
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)
    assert _first_event(guard, "tool_invoke").metadata["adapter"] == "langgraph"
    assert _first_event(guard, "llm_input").metadata["adapter"] == "langgraph"


def test_attach_langgraph_patches_builder_toolnode():
    class Tool:
        name = "lookup"

        def func(self, value: str) -> str:
            return value.upper()

    class ToolNode:
        __module__ = "langgraph.prebuilt.tool_node"

        def __init__(self) -> None:
            self.tools_by_name = {"lookup": Tool()}

    class StateNodeSpec:
        __module__ = "langgraph.graph.state"

        def __init__(self, runnable) -> None:
            self.runnable = runnable

    class Builder:
        __module__ = "langgraph.graph.state"

        def __init__(self) -> None:
            self.nodes = {"tools": StateNodeSpec(ToolNode())}

    class Graph:
        __module__ = "langgraph.graph.state"

        def __init__(self) -> None:
            self.builder = Builder()

        def invoke(self, state):
            return state

    guard = AgentGuard("attach-langgraph-builder", sandbox="noop")
    graph = Graph()

    patched = guard.attach_langgraph(graph, wrap_llm=False)
    result = graph.builder.nodes["tools"].runnable.tools_by_name["lookup"].func(value="abc")

    assert patched == {"tools": 1, "llm": 0}
    assert result == "ABC"
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)


def test_attach_langgraph_tool_invoke_deny_returns_toolmessage_compatible_value(monkeypatch):
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

    class ToolNode:
        __module__ = "langgraph.prebuilt.tool_node"

        def __init__(self) -> None:
            self.tools_by_name = {"shell_exec": Tool()}

    class PregelNode:
        __module__ = "langgraph.pregel._read"

        def __init__(self, bound) -> None:
            self.bound = bound

    class Graph:
        __module__ = "langgraph.graph.state"

        def __init__(self) -> None:
            self.nodes = {"tools": PregelNode(ToolNode())}

        def invoke(self, state):
            return state

    monkeypatch.setattr(langchain_adapter, "_get_langchain_tool_message_class", lambda: FakeToolMessage)

    guard = AgentGuard("attach-langgraph-deny", sandbox="noop", plugin_config=plugin_config)
    graph = Graph()

    patched = guard.attach_langgraph(graph, wrap_llm=False)
    result = graph.nodes["tools"].bound.tools_by_name["shell_exec"].invoke(
        {
            "id": "tool-call-lg-1",
            "name": "shell_exec",
            "type": "tool_call",
            "args": {
                "command": "rm -rf /tmp/demo",
            },
        }
    )

    assert patched == {"tools": 1, "llm": 0}
    assert calls == []
    assert isinstance(result, FakeToolMessage)
    assert result.name == "shell_exec"
    assert result.tool_call_id == "tool-call-lg-1"
    payload = json.loads(result.content)
    assert payload["agentguard"] == "blocked"
    assert payload["decision"] == "deny"
    assert "Destructive shell command blocked by local plugin." in payload["reason"]


def test_langgraph_and_langchain_adapter_boundaries():
    class PureLangGraph:
        __module__ = "langgraph.graph.state"

        def __init__(self) -> None:
            self.nodes = {}

        def invoke(self, state):
            return state

    class LangChainAgent:
        __module__ = "langchain.agents.factory"

        def invoke(self, prompt):
            return prompt

    langgraph = langgraph_adapter.LangGraphAgentAdapter()
    langchain = langchain_adapter.LangChainAgentAdapter()

    assert langgraph.can_wrap(PureLangGraph()) is True
    assert langchain.can_wrap(PureLangGraph()) is False
    assert langgraph.can_wrap(LangChainAgent()) is False
    assert langchain.can_wrap(LangChainAgent()) is True


@pytest.mark.asyncio
async def test_attach_openai_agents_patches_async_on_invoke_tool():
    class FunctionTool:
        name = "send"
        description = "Send a message."
        params_json_schema = {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }

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
    assert guard._registry.metadata("send").required_args == ["message"]
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)
    assert _first_event(guard, "llm_output").metadata["output_type"] == "dict"


class _FakeLlamaToolMetadata:
    def __init__(self, name: str, description: str = "", required: list[str] | None = None):
        self.name = name
        self.description = description
        self.return_direct = False
        self._required = required or []

    def get_name(self) -> str:
        return self.name

    def get_parameters_dict(self) -> dict:
        return {
            "type": "object",
            "properties": {name: {"type": "string"} for name in self._required},
            "required": list(self._required),
        }


class _FakeLlamaToolOutput:
    def __init__(
        self,
        *,
        content: str,
        tool_name: str,
        raw_input: dict | None = None,
        raw_output=None,
        is_error: bool = False,
    ) -> None:
        self.content = content
        self.tool_name = tool_name
        self.raw_input = raw_input or {}
        self.raw_output = raw_output
        self.is_error = is_error

    def __str__(self) -> str:
        return self.content


class _FakeLlamaChatMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content
        self.additional_kwargs = {}


class _FakeLlamaChatResponse:
    def __init__(self, content: str, delta: str | None = None) -> None:
        self.message = _FakeLlamaChatMessage("assistant", content)
        self.delta = delta
        self.raw = {"content": content}
        self.additional_kwargs = {}


def test_agentguard_exposes_attach_llamaindex():
    guard = AgentGuard("attach-llamaindex-api", sandbox="noop")

    assert hasattr(guard, "attach_llamaindex")


@pytest.mark.asyncio
async def test_attach_llamaindex_patches_workflow_agent_tool_and_llm():
    calls = []

    class Tool:
        metadata = _FakeLlamaToolMetadata("lookup", "Lookup a value.", ["value"])

        async def acall(self, **kwargs):
            calls.append(kwargs)
            return _FakeLlamaToolOutput(
                content=kwargs["value"].upper(),
                tool_name=self.metadata.name,
                raw_input=kwargs,
                raw_output=kwargs["value"].upper(),
            )

    class LLM:
        async def achat(self, messages):
            return _FakeLlamaChatResponse(f"reply:{messages[0].content}")

    class Agent:
        def __init__(self) -> None:
            self.tools = [Tool()]
            self.llm = LLM()

        async def _call_tool(self, ctx, tool, tool_input):
            return await tool.acall(**tool_input)

    guard = AgentGuard("attach-llamaindex", sandbox="noop")
    agent = Agent()

    patched = guard.attach_llamaindex(agent)
    tool_result = await agent._call_tool(None, agent.tools[0], {"value": "abc"})
    llm_result = await agent.llm.achat([_FakeLlamaChatMessage("user", "hello")])

    assert patched == {"tools": 1, "llm": 1}
    assert tool_result.content == "ABC"
    assert llm_result.message.content == "reply:hello"
    assert calls == [{"value": "abc"}]
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)
    assert "llm_input" in _event_types(guard)
    assert "llm_output" in _event_types(guard)
    event = _first_event(guard, "tool_invoke")
    assert event.payload.tool_name == "lookup"
    assert event.payload.arguments == {"value": "abc"}


@pytest.mark.asyncio
async def test_attach_llamaindex_agentworkflow_patches_nested_agents():
    class Tool:
        def __init__(self, name: str) -> None:
            self.metadata = _FakeLlamaToolMetadata(name, f"{name} tool.", ["value"])

        async def acall(self, **kwargs):
            return _FakeLlamaToolOutput(
                content=f"{self.metadata.name}:{kwargs['value']}",
                tool_name=self.metadata.name,
                raw_input=kwargs,
                raw_output=kwargs["value"],
            )

    class LLM:
        async def achat(self, messages):
            return _FakeLlamaChatResponse(messages[0].content)

    class Agent:
        def __init__(self, name: str) -> None:
            self.tools = [Tool(name)]
            self.llm = LLM()

        async def _call_tool(self, ctx, tool, tool_input):
            return await tool.acall(**tool_input)

    class Workflow:
        def __init__(self) -> None:
            self.agents = {"one": Agent("one"), "two": Agent("two")}

    guard = AgentGuard("attach-llamaindex-workflow", sandbox="noop")
    workflow = Workflow()

    patched = guard.attach_llamaindex(workflow)
    result = await workflow.agents["two"]._call_tool(
        None,
        workflow.agents["two"].tools[0],
        {"value": "ok"},
    )

    assert patched == {"tools": 2, "llm": 2}
    assert result.content == "two:ok"
    assert _event_types(guard).count("tool_invoke") == 1
    assert _event_types(guard).count("tool_result") == 1


@pytest.mark.asyncio
async def test_attach_llamaindex_tool_deny_returns_tooloutput_compatible_value(monkeypatch):
    from agentguard.adapters.agent import llamaindex as llamaindex_adapter

    calls = []
    plugin_config = {
        "phases": {
            "tool_before": {
                "client": ["tool_invoke"],
                "server": [],
            }
        }
    }

    class Tool:
        metadata = _FakeLlamaToolMetadata("shell_exec", "Run a shell command.", ["command"])
        capabilities = ["shell"]

        async def acall(self, **kwargs):
            calls.append(kwargs)
            return _FakeLlamaToolOutput(
                content="ran",
                tool_name=self.metadata.name,
                raw_input=kwargs,
                raw_output="ran",
            )

    class Agent:
        def __init__(self) -> None:
            self.tools = [Tool()]
            self.llm = object()

        async def _call_tool(self, ctx, tool, tool_input):
            return await tool.acall(**tool_input)

    llamaindex_adapter._get_llamaindex_tool_output_class.cache_clear()
    monkeypatch.setattr(
        llamaindex_adapter,
        "_get_llamaindex_tool_output_class",
        lambda: _FakeLlamaToolOutput,
    )

    guard = AgentGuard("attach-llamaindex-deny", sandbox="noop", plugin_config=plugin_config)
    agent = Agent()

    patched = guard.attach_llamaindex(agent, wrap_llm=False)
    result = await agent._call_tool(None, agent.tools[0], {"command": "rm -rf /tmp/demo"})

    assert patched == {"tools": 1, "llm": 0}
    assert calls == []
    assert isinstance(result, _FakeLlamaToolOutput)
    assert result.tool_name == "shell_exec"
    assert result.raw_input == {"command": "rm -rf /tmp/demo"}
    assert result.is_error is True
    assert result.raw_output["agentguard"] == "blocked"
    assert result.raw_output["decision"] == "deny"
    assert "Destructive shell command blocked by local plugin." in result.content
    assert _event_types(guard).count("tool_invoke") == 1
    assert "tool_result" not in _event_types(guard)


@pytest.mark.asyncio
async def test_attach_llamaindex_streaming_llm_emits_after_output():
    class LLM:
        async def astream_chat(self, messages):
            async def stream():
                yield _FakeLlamaChatResponse("h", delta="h")
                yield _FakeLlamaChatResponse("hi", delta="i")

            return stream()

    class Agent:
        def __init__(self) -> None:
            self.tools = []
            self.llm = LLM()

        async def _call_tool(self, ctx, tool, tool_input):
            raise AssertionError("tool should not be called")

    guard = AgentGuard("attach-llamaindex-stream", sandbox="noop")
    agent = Agent()

    patched = guard.attach_llamaindex(agent, wrap_tools=False)
    stream = await agent.llm.astream_chat([_FakeLlamaChatMessage("user", "hello")])
    chunks = [chunk async for chunk in stream]

    assert patched == {"tools": 0, "llm": 1}
    assert [chunk.message.content for chunk in chunks] == ["h", "hi"]
    assert _event_types(guard).count("llm_input") == 1
    assert _event_types(guard).count("llm_output") == 1
    assert _first_event(guard, "llm_output").metadata["label"] == "astream_chat"
    assert _first_event(guard, "tool_invoke").payload.arguments == {"message": "hello"}


@pytest.mark.asyncio
async def test_attach_openai_agents_uses_get_all_tools_when_tools_are_deferred():
    class FunctionTool:
        name = "send"

        async def on_invoke_tool(self, run_context, json_input: str) -> str:
            args = json.loads(json_input)
            return f"sent:{args['message']}"

    class Agent:
        def __init__(self) -> None:
            self.tools = []
            self._resolved_tool = FunctionTool()

        async def get_all_tools(self, run_context):
            _ = run_context
            return [self._resolved_tool]

    guard = AgentGuard("attach-openai-get-all-tools", sandbox="noop")
    agent = Agent()

    patched = guard.attach_openai_agents(agent)
    result = await agent._resolved_tool.on_invoke_tool(None, '{"message": "hello"}')

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert result == "sent:hello"
    assert "tool_invoke" in _event_types(guard)
    assert "tool_result" in _event_types(guard)


@pytest.mark.asyncio
async def test_attach_openai_agents_extracts_nested_input_payload_and_real_tool_args():
    class FunctionTool:
        name = "send"

        def __init__(self) -> None:
            self.input_schema = {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["url", "body"],
            }

        async def on_invoke_tool(self, ctx, input: dict[str, object]) -> str:
            payload = input.get("input") if isinstance(input, dict) else None
            assert isinstance(payload, str)
            args = json.loads(payload)
            return f"sent:{args['url']}:{args['body']}"

    class Agent:
        def __init__(self) -> None:
            self.tools = [FunctionTool()]

    guard = AgentGuard("attach-openai-nested-input", sandbox="noop")
    agent = Agent()

    patched = guard.attach_openai_agents(agent, wrap_llm=False)
    result = await agent.tools[0].on_invoke_tool(
        None,
        {
            "ctx": "ignored",
            "input": '{"url": "https://example.com", "body": "secret"}',
        },
    )

    assert patched["tools"] == 1
    assert patched["llm"] == 0
    assert result == "sent:https://example.com:secret"
    assert guard._registry.metadata("send").required_args == ["url", "body"]
    assert _first_event(guard, "tool_invoke").payload.arguments == {
        "url": "https://example.com",
        "body": "secret",
    }


@pytest.mark.asyncio
async def test_attach_openai_agents_patches_model_get_response_without_double_wrapping_client():
    class Responses:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            return {"content": kwargs["input"]}

    class Client:
        def __init__(self) -> None:
            self.responses = Responses()

    class Model:
        def __init__(self) -> None:
            self._client = Client()

        async def get_response(self, prompt: str) -> dict[str, str]:
            return await self._client.responses.create(input=prompt)

    class Agent:
        def __init__(self) -> None:
            self.model = Model()
            self.tools = []

    guard = AgentGuard("attach-openai-model-get-response", sandbox="noop")
    agent = Agent()

    patched = guard.attach_openai_agents(agent, wrap_tools=False)
    result = await agent.model.get_response("hello")

    assert patched["tools"] == 0
    assert patched["llm"] == 1
    assert result == {"content": "hello"}
    assert agent.model._client.responses.calls == 1
    assert _event_types(guard).count("llm_input") == 1
    assert _event_types(guard).count("llm_output") == 1


@pytest.mark.asyncio
async def test_attach_openai_agents_patches_string_model_via_runner(monkeypatch):
    class FakeModel:
        def __init__(self) -> None:
            self.calls = 0

        async def get_response(self, prompt: str) -> dict[str, str]:
            self.calls += 1
            return {"content": prompt}

    class FakeProvider:
        def __init__(self) -> None:
            self.models: list[FakeModel] = []

        def get_model(self, model_name: str) -> FakeModel:
            _ = model_name
            model = FakeModel()
            self.models.append(model)
            return model

    class RunConfig:
        def __init__(self, model_provider=None) -> None:
            self.model_provider = model_provider or FakeProvider()

    class Runner:
        @classmethod
        async def run(cls, starting_agent, prompt: str, run_config=None):
            _ = cls
            cfg = run_config or RunConfig()
            model = cfg.model_provider.get_model(starting_agent.model)
            return await model.get_response(prompt)

    fake_agents = types.ModuleType("agents")
    fake_agents.RunConfig = RunConfig
    fake_agents.Runner = Runner
    monkeypatch.setitem(__import__("sys").modules, "agents", fake_agents)

    class Agent:
        def __init__(self) -> None:
            self.model = "gpt-4o-mini"
            self.tools = []

    guard = AgentGuard("attach-openai-string-model", sandbox="noop")
    agent = Agent()

    patched = guard.attach_openai_agents(agent, wrap_tools=False)
    result = await Runner.run(agent, "hello")

    assert patched["tools"] == 0
    assert patched["llm"] == 1
    assert result == {"content": "hello"}
    assert _event_types(guard).count("llm_input") == 1
    assert _event_types(guard).count("llm_output") == 1
