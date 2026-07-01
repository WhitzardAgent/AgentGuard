import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager

from agentguard.schemas.decisions import GuardDecision


def _install_fake_dify_modules(monkeypatch):
    dify_agent = types.ModuleType("dify_agent")
    adapters = types.ModuleType("dify_agent.adapters")
    llm_pkg = types.ModuleType("dify_agent.adapters.llm")
    model_mod = types.ModuleType("dify_agent.adapters.llm.model")
    layers = types.ModuleType("dify_agent.layers")
    dify_plugin = types.ModuleType("dify_agent.layers.dify_plugin")
    tools_mod = types.ModuleType("dify_agent.layers.dify_plugin.tools_layer")
    runtime = types.ModuleType("dify_agent.runtime")
    runner_mod = types.ModuleType("dify_agent.runtime.runner")

    class DifyLLMAdapterModel:
        model_provider = "openai"
        model_name = "gpt-test"
        system = "DifyPlugin/plugin-1"

        def prepare_request(self, model_settings, model_request_parameters):
            return model_settings, model_request_parameters

        def _build_request_input(self, messages, model_settings, model_request_parameters):
            return types.SimpleNamespace(
                prompt_messages=[types.SimpleNamespace(content="hello from dify")],
                model_parameters={},
                tools=None,
            )

        async def request(self, messages, model_settings, model_request_parameters):
            return types.SimpleNamespace(parts=[types.SimpleNamespace(content="llm answer")])

        @asynccontextmanager
        async def request_stream(
            self,
            messages,
            model_settings,
            model_request_parameters,
            run_context=None,
        ):
            yield types.SimpleNamespace(
                get=lambda: types.SimpleNamespace(parts=[types.SimpleNamespace(content="stream answer")])
            )

    class AgentRunRunner:
        def __init__(self):
            self.run_id = "dify-run-1"
            self.request = types.SimpleNamespace(
                metadata={
                    "tenant_id": "tenant-1",
                    "app_id": "app-1",
                    "workflow_id": "workflow-1",
                    "workflow_run_id": "workflow-run-1",
                    "node_id": "agent-node-1",
                    "node_execution_id": "node-exec-1",
                    "user_id": "user-1",
                }
            )

        async def _run_agent(self):
            model = DifyLLMAdapterModel()
            await model.request([], None, None)
            tool = tools_mod._build_pydantic_ai_tool(
                client=FakeToolClient(),
                tool_config=FakeToolConfig(),
                effective_parameters=[],
            )
            return await tool.fn(None, query="weather")

    class ToolDefinition:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Tool:
        def __init__(self, fn, **kwargs):
            self.fn = fn
            self.kwargs = kwargs
            self.name = kwargs.get("name")

    class DifyPluginToolClientError(Exception):
        error_type = "ToolError"
        status_code = 400

    class FakeToolClient:
        async def invoke(self, **kwargs):
            return [{"text": f"result:{kwargs['tool_parameters']['query']}"}]

    class FakeToolConfig:
        plugin_id = "plugin-1"
        provider = "local_bing_web_search"
        tool_name = "web_search"
        credential_type = "unauthorized"
        credentials = {}
        name = "web_search"
        description = "Search"
        parameters_json_schema = {"type": "object"}

    def _build_pydantic_ai_tool(*, client, tool_config, effective_parameters):
        async def invoke_tool(_ctx, **tool_arguments):
            merged = _prepare_tool_arguments(effective_parameters, tool_config, tool_arguments)
            messages = await client.invoke(
                provider=tool_config.provider,
                tool_name=tool_config.tool_name,
                credential_type=tool_config.credential_type,
                credentials=dict(tool_config.credentials),
                tool_parameters=merged,
            )
            return _convert_tool_response_to_text(messages)

        return Tool(invoke_tool, takes_ctx=True, name=tool_config.name, description=tool_config.description)

    def _prepare_tool_arguments(_effective_parameters, _tool_config, tool_arguments):
        return dict(tool_arguments)

    def _convert_tool_response_to_text(messages):
        return "|".join(str(item) for item in messages)

    def _tool_error_text(*, tool_name, error):
        return f"tool invoke error: {tool_name}:{error}"

    model_mod.DifyLLMAdapterModel = DifyLLMAdapterModel
    runner_mod.AgentRunRunner = AgentRunRunner
    tools_mod.Tool = Tool
    tools_mod.ToolDefinition = ToolDefinition
    tools_mod.PLUGIN_TOOL_STRICT = False
    tools_mod.DifyPluginToolClientError = DifyPluginToolClientError
    tools_mod._build_pydantic_ai_tool = _build_pydantic_ai_tool
    tools_mod._prepare_tool_arguments = _prepare_tool_arguments
    tools_mod._convert_tool_response_to_text = _convert_tool_response_to_text
    tools_mod._tool_error_text = _tool_error_text
    dify_plugin.tools_layer = tools_mod

    modules = {
        "dify_agent": dify_agent,
        "dify_agent.adapters": adapters,
        "dify_agent.adapters.llm": llm_pkg,
        "dify_agent.adapters.llm.model": model_mod,
        "dify_agent.layers": layers,
        "dify_agent.layers.dify_plugin": dify_plugin,
        "dify_agent.layers.dify_plugin.tools_layer": tools_mod,
        "dify_agent.runtime": runtime,
        "dify_agent.runtime.runner": runner_mod,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(
        model_mod=model_mod,
        runner_mod=runner_mod,
        tools_mod=tools_mod,
        FakeToolClient=FakeToolClient,
        FakeToolConfig=FakeToolConfig,
    )


def _install_fake_legacy_dify_modules(monkeypatch):
    core = types.ModuleType("core")
    model_manager_mod = types.ModuleType("core.model_manager")
    plugin_pkg = types.ModuleType("core.plugin")
    backwards_pkg = types.ModuleType("core.plugin.backwards_invocation")
    backwards_model_mod = types.ModuleType("core.plugin.backwards_invocation.model")
    backwards_tool_mod = types.ModuleType("core.plugin.backwards_invocation.tool")
    tools_pkg = types.ModuleType("core.tools")
    tool_engine_mod = types.ModuleType("core.tools.tool_engine")
    tool_entities_mod = types.ModuleType("core.tools.entities.tool_entities")
    workflow_pkg = types.ModuleType("core.workflow")
    node_factory_mod = types.ModuleType("core.workflow.node_factory")
    nodes_pkg = types.ModuleType("core.workflow.nodes")
    agent_pkg = types.ModuleType("core.workflow.nodes.agent")
    agent_node_mod = types.ModuleType("core.workflow.nodes.agent.agent_node")
    app_pkg = types.ModuleType("core.app")
    app_entities_pkg = types.ModuleType("core.app.entities")
    app_invoke_mod = types.ModuleType("core.app.entities.app_invoke_entities")

    app_invoke_mod.DIFY_RUN_CONTEXT_KEY = "dify_run_context"

    class DifyRunContext:
        @classmethod
        def model_validate(cls, value):
            return value

    app_invoke_mod.DifyRunContext = DifyRunContext

    class ModelInstance:
        model_name = "gpt-4o-mini"
        provider = "langgenius/openai/openai"

        def invoke_llm(
            self,
            prompt_messages,
            model_parameters=None,
            tools=None,
            stop=None,
            stream=True,
            callbacks=None,
        ):
            if stream:
                def chunks():
                    yield types.SimpleNamespace(
                        delta=types.SimpleNamespace(
                            message=types.SimpleNamespace(content="thinking", tool_calls=[]),
                            usage=None,
                        )
                    )
                    yield types.SimpleNamespace(
                        delta=types.SimpleNamespace(
                            message=types.SimpleNamespace(content="", tool_calls=[{"name": "web_search"}]),
                            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                        )
                    )

                return chunks()
            return types.SimpleNamespace(
                message=types.SimpleNamespace(content="final answer", tool_calls=[]),
                usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
                prompt_messages=prompt_messages,
            )

    model_manager_mod.ModelInstance = ModelInstance

    class ToolInvokeMeta:
        def __init__(self, error=None):
            self.error = error

        @classmethod
        def error_instance(cls, text):
            return cls(error=text)

        def to_dict(self):
            return {"error": self.error}

    tool_entities_mod.ToolInvokeMeta = ToolInvokeMeta

    class ToolInvokeMessage:
        def __init__(self, type="text", message=None, **kwargs):
            self.type = type
            self.message = types.SimpleNamespace(**(message or {})) if isinstance(message, dict) else message
            self.kwargs = kwargs

    tool_entities_mod.ToolInvokeMessage = ToolInvokeMessage

    class ToolEngine:
        calls = []
        generic_calls = []

        @staticmethod
        def agent_invoke(
            tool,
            tool_parameters,
            user_id,
            tenant_id,
            message,
            invoke_from,
            agent_tool_callback,
            trace_manager=None,
            conversation_id=None,
            app_id=None,
            message_id=None,
        ):
            ToolEngine.calls.append((tool.entity.identity.name, dict(tool_parameters)))
            return f"tool result:{tool_parameters['q']}", [], ToolInvokeMeta()

        @staticmethod
        def generic_invoke(
            tool,
            tool_parameters,
            user_id,
            workflow_tool_callback,
            workflow_call_depth,
            conversation_id=None,
            app_id=None,
            message_id=None,
        ):
            ToolEngine.generic_calls.append((tool.entity.identity.name, dict(tool_parameters)))

            def chunks():
                yield ToolInvokeMessage(type="text", message={"text": f"workflow tool result:{tool_parameters['q']}"})

            return chunks()

    tool_engine_mod.ToolEngine = ToolEngine

    class FakeTool:
        def __init__(self):
            self.entity = types.SimpleNamespace(
                identity=types.SimpleNamespace(
                    name="web_search",
                    provider="local_bing_web_search",
                    icon="",
                )
            )
            self.runtime = types.SimpleNamespace(runtime_parameters={"format": "rss"})

        def tool_provider_type(self):
            return types.SimpleNamespace(value="api")

    class AgentNode:
        def __init__(self):
            self._node_id = "1782713638856"
            self.id = "node-exec-1"
            self.node_data = types.SimpleNamespace(
                agent_strategy_name="function_calling",
                agent_strategy_provider_name="langgenius/agent/agent",
            )
            self.graph_init_params = types.SimpleNamespace(
                workflow_id="workflow-1",
                workflow_run_id="workflow-run-1",
            )
            self.dify_context = types.SimpleNamespace(
                tenant_id="tenant-1",
                user_id="user-1",
                app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
                workflow_id="workflow-1",
                workflow_run_id="workflow-run-1",
                invoke_from="debugger",
            )

        def require_run_context_value(self, _key):
            return self.dify_context

        def _run(self):
            model = ModelInstance()
            for chunk in model.invoke_llm(
                [types.SimpleNamespace(content="query")],
                tools=[types.SimpleNamespace(name="web_search")],
                stream=True,
            ):
                yield chunk
            yield ToolEngine.agent_invoke(
                FakeTool(),
                {"q": "today news"},
                "user-1",
                "tenant-1",
                types.SimpleNamespace(id="message-1", conversation_id="conversation-1"),
                "debugger",
                types.SimpleNamespace(),
                app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
                message_id="message-1",
                conversation_id="conversation-1",
            )

    agent_node_mod.AgentNode = AgentNode

    class DifyNodeFactory:
        def __init__(self):
            self.graph_init_params = types.SimpleNamespace(
                workflow_id="workflow-1",
                run_context={
                    "dify_run_context": types.SimpleNamespace(
                        tenant_id="tenant-1",
                        user_id="user-1",
                        app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
                        workflow_id="workflow-1",
                        workflow_run_id="workflow-run-1",
                        invoke_from="debugger",
                    )
                },
            )
            self._dify_context = self.graph_init_params.run_context["dify_run_context"]

        def create_node(self, node_config):
            node_type = node_config["data"]["type"]
            if node_type == "tool":
                return WorkflowToolNode(node_config)
            if node_type in {"if-else", "human-input", "iteration", "loop"}:
                return WorkflowLogicNode(node_config)
            if node_type == "code":
                return WorkflowGenericNode(node_config)
            return WorkflowLLMNode(node_config)

    class WorkflowLLMNode:
        def __init__(self, node_config):
            self.node_id = node_config["id"]
            self.id = node_config["id"]
            self.node_data = types.SimpleNamespace(**node_config["data"])
            self.graph_init_params = types.SimpleNamespace(workflow_id="workflow-1")
            self.execution_id = f"{node_config['id']}-exec"

        def run(self):
            model = ModelInstance()
            for chunk in model.invoke_llm(
                [types.SimpleNamespace(content=f"{self.node_data.type} query")],
                tools=None,
                stream=True,
            ):
                yield chunk

    class WorkflowToolNode:
        def __init__(self, node_config):
            self.node_id = node_config["id"]
            self.id = node_config["id"]
            self.node_data = types.SimpleNamespace(**node_config["data"])
            self.graph_init_params = types.SimpleNamespace(workflow_id="workflow-1")
            self.execution_id = f"{node_config['id']}-exec"

        def run(self):
            yield from ToolEngine.generic_invoke(
                FakeTool(),
                {"q": "today news"},
                "user-1",
                types.SimpleNamespace(),
                0,
                conversation_id="conversation-1",
                app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
                message_id="message-1",
            )

    class WorkflowGenericNode:
        def __init__(self, node_config):
            self.node_id = node_config["id"]
            self.id = node_config["id"]
            self.node_data = types.SimpleNamespace(**node_config["data"])
            self.graph_init_params = types.SimpleNamespace(workflow_id="workflow-1")
            self.execution_id = f"{node_config['id']}-exec"
            self.inputs = {"value": "raw"}

        def run(self):
            yield types.SimpleNamespace(outputs={"result": "processed"})

    class WorkflowLogicNode:
        def __init__(self, node_config):
            self.node_id = node_config["id"]
            self.id = node_config["id"]
            self.node_data = types.SimpleNamespace(**node_config["data"])
            self.graph_init_params = types.SimpleNamespace(workflow_id="workflow-1")
            self.execution_id = f"{node_config['id']}-exec"

        def run(self):
            yield types.SimpleNamespace(outputs={"routed": self.node_data.type})

    node_factory_mod.DifyNodeFactory = DifyNodeFactory

    class PluginModelBackwardsInvocation:
        @classmethod
        def invoke_llm(cls, user_id, tenant, payload):
            return ModelInstance().invoke_llm(
                prompt_messages=payload.prompt_messages,
                model_parameters=payload.completion_params,
                tools=payload.tools,
                stop=payload.stop,
                stream=payload.stream,
            )

    class PluginToolBackwardsInvocation:
        calls = []

        @classmethod
        def invoke_tool(
            cls,
            tenant_id,
            user_id,
            tool_type,
            provider,
            tool_name,
            tool_parameters,
            credential_id=None,
        ):
            cls.calls.append((tool_name, dict(tool_parameters)))

            def chunks():
                yield ToolInvokeMessage(type="text", message={"text": f"plugin tool result:{tool_parameters['q']}"})

            return chunks()

    backwards_model_mod.PluginModelBackwardsInvocation = PluginModelBackwardsInvocation
    backwards_tool_mod.PluginToolBackwardsInvocation = PluginToolBackwardsInvocation

    modules = {
        "core": core,
        "core.model_manager": model_manager_mod,
        "core.plugin": plugin_pkg,
        "core.plugin.backwards_invocation": backwards_pkg,
        "core.plugin.backwards_invocation.model": backwards_model_mod,
        "core.plugin.backwards_invocation.tool": backwards_tool_mod,
        "core.tools": tools_pkg,
        "core.tools.tool_engine": tool_engine_mod,
        "core.tools.entities.tool_entities": tool_entities_mod,
        "core.workflow": workflow_pkg,
        "core.workflow.node_factory": node_factory_mod,
        "core.workflow.nodes": nodes_pkg,
        "core.workflow.nodes.agent": agent_pkg,
        "core.workflow.nodes.agent.agent_node": agent_node_mod,
        "core.app": app_pkg,
        "core.app.entities": app_entities_pkg,
        "core.app.entities.app_invoke_entities": app_invoke_mod,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(
        ModelInstance=ModelInstance,
        ToolEngine=ToolEngine,
        AgentNode=AgentNode,
        DifyNodeFactory=DifyNodeFactory,
        WorkflowLLMNode=WorkflowLLMNode,
        WorkflowToolNode=WorkflowToolNode,
        WorkflowGenericNode=WorkflowGenericNode,
        WorkflowLogicNode=WorkflowLogicNode,
        FakeTool=FakeTool,
        PluginModelBackwardsInvocation=PluginModelBackwardsInvocation,
        PluginToolBackwardsInvocation=PluginToolBackwardsInvocation,
    )


def _fresh_adapter(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_ENABLED", raising=False)
    import agentguard.adapters.agent.dify as dify_adapter

    return importlib.reload(dify_adapter)


def _event_types(guard) -> list[str]:
    return [entry.event.event_type.value for entry in guard.trace.entries]


def test_install_dify_adapter_disabled_is_noop(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_ENABLED", "false")
    import agentguard.adapters.agent.dify as dify_adapter

    dify_adapter = importlib.reload(dify_adapter)
    status = dify_adapter.install_dify_adapter()

    assert status == {"enabled": False, "patched": False, "reason": "disabled"}
    assert not getattr(fake.runner_mod.AgentRunRunner._run_agent, "__agentguard_dify_patched__", False)


def test_install_dify_adapter_without_dify_is_noop(monkeypatch):
    for name in list(sys.modules):
        if name == "dify_agent" or name.startswith("dify_agent."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    dify_adapter = _fresh_adapter(monkeypatch)

    status = dify_adapter.install_dify_adapter()

    assert status["enabled"] is True
    assert status["patched"] is False
    assert status["details"]["agent_v2"]["reason"] == "dify_import_failed"
    assert status["details"]["legacy_api"]["reason"] == "legacy_import_failed"


def test_install_dify_adapter_is_idempotent(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    first = dify_adapter.install_dify_adapter()
    second = dify_adapter.install_dify_adapter()

    assert first["patched"] is True
    assert second["patched"] is False
    assert getattr(fake.runner_mod.AgentRunRunner._run_agent, "__agentguard_dify_patched__", False)
    assert getattr(fake.model_mod.DifyLLMAdapterModel.request, "__agentguard_dify_patched__", False)
    assert getattr(fake.tools_mod._build_pydantic_ai_tool, "__agentguard_dify_patched__", False)


def test_dify_runner_llm_and_tool_hooks_emit_events(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("runner-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)

    runner = fake.runner_mod.AgentRunRunner()
    result = asyncio.run(runner._run_agent())
    guard = dify_adapter._current_guard.get()

    assert "result:weather" in result
    assert guard is None
    assert len(created_guards) == 1
    assert _event_types(created_guards[0]) == [
        "llm_input",
        "llm_output",
        "tool_invoke",
        "tool_result",
    ]
    assert [tool.name for tool in created_guards[0].reported_tools] == ["web_search"]


def test_dify_llm_request_wrapper_emits_before_and_after(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    from agentguard import AgentGuard

    guard = AgentGuard("dify-llm-test", sandbox="noop")
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set({"dify_agent_run_id": "run-llm"})
    try:
        response = asyncio.run(fake.model_mod.DifyLLMAdapterModel().request([], None, None))
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert response.parts[0].content == "llm answer"
    assert _event_types(guard) == ["llm_input", "llm_output"]
    assert guard.trace.entries[0].event.payload.messages[0]["content"] == "hello from dify"
    assert guard.trace.entries[0].event.metadata["adapter"] == "dify"


def test_dify_llm_request_stream_wrapper_emits_before_and_after(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    from agentguard import AgentGuard

    async def run_stream():
        async with fake.model_mod.DifyLLMAdapterModel().request_stream([], None, None) as streamed:
            assert streamed.get().parts[0].content == "stream answer"

    guard = AgentGuard("dify-llm-stream-test", sandbox="noop")
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set({"dify_agent_run_id": "run-stream"})
    try:
        asyncio.run(run_stream())
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert _event_types(guard) == ["llm_input", "llm_output"]
    assert guard.trace.entries[1].event.payload.output == "stream answer"
    assert guard.trace.entries[1].event.metadata["stream"] is True


def test_dify_tool_wrapper_emits_before_and_after(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    from agentguard import AgentGuard

    guard = AgentGuard("dify-tool-test", sandbox="noop")
    guard.reported_tools = []
    guard._report_tool_metadata = guard.reported_tools.append
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set({"dify_agent_run_id": "run-tool"})
    try:
        tool = fake.tools_mod._build_pydantic_ai_tool(
            client=fake.FakeToolClient(),
            tool_config=fake.FakeToolConfig(),
            effective_parameters=[],
        )
        result = asyncio.run(tool.fn(None, query="weather"))
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert "result:weather" in result
    assert _event_types(guard) == ["tool_invoke", "tool_result"]
    invoke = guard.trace.entries[0].event
    assert invoke.payload.tool_name == "web_search"
    assert invoke.payload.arguments == {"query": "weather"}
    assert invoke.metadata["plugin_id"] == "plugin-1"
    assert len(guard.reported_tools) == 1
    assert guard.reported_tools[0].name == "web_search"
    assert guard.reported_tools[0].schema == {"type": "object"}


def test_dify_tool_before_deny_skips_original_tool(monkeypatch):
    fake = _install_fake_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    class DenyRuntime:
        def __init__(self):
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase))
            decision = (
                GuardDecision.deny("blocked search")
                if event.event_type.value == "tool_invoke"
                else GuardDecision.allow()
            )
            return types.SimpleNamespace(decision=decision)

    class DenyGuard:
        def __init__(self):
            self.runtime = DenyRuntime()
            self.context = types.SimpleNamespace(session_id="deny")

    class ExplodingClient:
        async def invoke(self, **_kwargs):
            raise AssertionError("original tool should not run")

    guard = DenyGuard()
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set({"dify_agent_run_id": "run-deny"})
    try:
        tool = fake.tools_mod._build_pydantic_ai_tool(
            client=ExplodingClient(),
            tool_config=fake.FakeToolConfig(),
            effective_parameters=[],
        )
        result = asyncio.run(tool.fn(None, query="weather"))
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert "blocked search" in result
    assert guard.runtime.calls == [("tool_invoke", "before")]


def test_install_dify_adapter_patches_legacy_api(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    first = dify_adapter.install_dify_adapter()
    second = dify_adapter.install_dify_adapter()

    assert first["patched"] is True
    assert first["details"]["workflow_api"]["patched"] is True
    assert first["details"]["legacy_api"]["patched"] is True
    assert first["details"]["agent_v2"]["patched"] is False
    assert second["details"]["workflow_api"]["patched"] is False
    assert second["details"]["legacy_api"]["patched"] is False
    assert getattr(fake.DifyNodeFactory.create_node, "__agentguard_dify_patched__", False)
    assert getattr(fake.AgentNode._run, "__agentguard_dify_patched__", False)
    assert getattr(fake.ModelInstance.invoke_llm, "__agentguard_dify_patched__", False)
    assert getattr(fake.ToolEngine.agent_invoke, "__agentguard_dify_patched__", False)
    assert getattr(fake.ToolEngine.generic_invoke, "__agentguard_dify_patched__", False)


def test_workflow_llm_node_emits_events(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("workflow-llm-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    node = fake.DifyNodeFactory().create_node(
        {"id": "1782718941283", "data": {"type": "llm", "title": "构造联网query"}}
    )

    chunks = list(node.run())

    assert len(chunks) == 2
    assert dify_adapter._current_guard.get() is None
    assert len(created_guards) == 1
    guard = created_guards[0]
    assert guard.context.session_id == "workflow-llm-test"
    assert dify_adapter._agent_id(guard.context.metadata) == "ce0aa322-1f3f-4ab9-8329-3af8588c7480:workflow-1"
    assert _event_types(guard) == ["llm_input", "llm_output"]
    assert guard.trace.entries[0].event.metadata["dify_runtime"] == "workflow_api"
    assert guard.trace.entries[0].event.metadata["node_type"] == "llm"
    assert guard.trace.entries[0].event.metadata["node_title"] == "构造联网query"
    assert guard.trace.entries[0].event.metadata["app_id"] == "ce0aa322-1f3f-4ab9-8329-3af8588c7480"
    assert guard.reported_tools == []


def test_workflow_make_guard_uses_workflow_run_session_and_stores_metadata(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    metadata = {
        "adapter": "dify",
        "dify_runtime": "workflow_api",
        "app_id": "app-1",
        "workflow_id": "workflow-1",
        "workflow_run_id": "workflow-run-1",
        "node_id": "node-1",
        "node_execution_id": "node-exec-1",
        "node_type": "code",
    }

    guard = dify_adapter._make_guard(metadata)

    assert guard.context.session_id == "workflow-run-1"
    assert guard.context.agent_id == "app-1:workflow-1"
    assert guard.context.metadata["node_execution_id"] == "node-exec-1"
    assert guard.context.metadata["node_type"] == "code"


def test_workflow_question_classifier_node_emits_llm_events(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("workflow-classifier-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    node = fake.DifyNodeFactory().create_node(
        {"id": "1782718852307", "data": {"type": "question-classifier", "title": "问题分类器"}}
    )

    list(node.run())

    assert len(created_guards) == 1
    assert _event_types(created_guards[0]) == ["llm_input", "llm_output"]
    assert created_guards[0].trace.entries[0].event.metadata["node_type"] == "question-classifier"
    assert created_guards[0].trace.entries[0].event.metadata["dify_runtime"] == "workflow_api"
    assert created_guards[0].reported_tools == []


def test_workflow_tool_node_emits_events(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("workflow-tool-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    fake.ToolEngine.generic_calls.clear()
    node = fake.DifyNodeFactory().create_node(
        {"id": "1782719127293", "data": {"type": "tool", "title": "web_search"}}
    )

    chunks = list(node.run())

    assert chunks[0].message.text == "workflow tool result:today news"
    assert fake.ToolEngine.generic_calls == [("web_search", {"q": "today news"})]
    assert len(created_guards) == 1
    guard = created_guards[0]
    assert _event_types(guard) == ["tool_invoke", "tool_result"]
    assert guard.trace.entries[0].event.payload.tool_name == "web_search"
    assert guard.trace.entries[0].event.payload.arguments == {"q": "today news"}
    assert guard.trace.entries[0].event.metadata["dify_runtime"] == "workflow_api"
    assert guard.trace.entries[0].event.metadata["node_type"] == "tool"
    assert "dify_workflow_tool" in guard.trace.entries[0].event.payload.capabilities
    assert len(guard.reported_tools) == 1
    assert guard.reported_tools[0].name == "web_search"


def test_workflow_tool_catalog_reports_again_for_new_guard(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard(f"workflow-tool-test-{len(created_guards)}", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)

    for _ in range(2):
        node = fake.DifyNodeFactory().create_node(
            {"id": "1782719127293", "data": {"type": "tool", "title": "web_search"}}
        )
        list(node.run())

    assert len(created_guards) == 2
    assert [guard.reported_tools[0].name for guard in created_guards] == ["web_search", "web_search"]


def test_workflow_generic_node_emits_tool_events(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("workflow-generic-node-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    node = fake.DifyNodeFactory().create_node(
        {"id": "1782719000000", "data": {"type": "code", "title": "Code"}}
    )

    chunks = list(node.run())

    assert chunks[0].outputs == {"result": "processed"}
    assert len(created_guards) == 1
    guard = created_guards[0]
    assert _event_types(guard) == ["tool_invoke", "tool_result"]
    assert guard.trace.entries[0].event.payload.tool_name == "dify_node:code:1782719000000"
    assert guard.trace.entries[0].event.payload.arguments["inputs"] == {"value": "raw"}
    assert guard.trace.entries[0].event.metadata["node_as_tool"] is True
    assert guard.trace.entries[1].event.payload.result == '{"result": "processed"}'
    assert guard.reported_tools[0].name == "dify_node:code:1782719000000"


def test_workflow_node_id_filter_does_not_skip_workflow_api_nodes(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_NODE_IDS", "some-other-node")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("workflow-node-filter-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    node = fake.DifyNodeFactory().create_node(
        {"id": "1782719000000", "data": {"type": "code", "title": "Code"}}
    )

    list(node.run())

    assert len(created_guards) == 1
    assert _event_types(created_guards[0]) == ["tool_invoke", "tool_result"]
    assert created_guards[0].trace.entries[0].event.metadata["node_id"] == "1782719000000"


def test_workflow_logic_nodes_are_not_guarded(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    created_guards = []

    def make_guard(metadata):
        created_guards.append(metadata)
        raise AssertionError("logic nodes should not create an AgentGuard session")

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)

    for node_type in ("if-else", "human-input", "iteration", "loop"):
        node = fake.DifyNodeFactory().create_node(
            {"id": f"{node_type}-node", "data": {"type": node_type, "title": node_type}}
        )
        chunks = list(node.run())
        assert chunks[0].outputs == {"routed": node_type}

    assert created_guards == []


def test_workflow_tool_before_deny_skips_original_tool(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    class DenyRuntime:
        def __init__(self):
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase))
            decision = (
                GuardDecision.deny("blocked web_search")
                if event.event_type.value == "tool_invoke"
                else GuardDecision.allow()
            )
            return types.SimpleNamespace(decision=decision)

    class DenyGuard:
        def __init__(self):
            self.runtime = DenyRuntime()
            self.context = types.SimpleNamespace(session_id="workflow-deny")

    guard = DenyGuard()
    fake.ToolEngine.generic_calls.clear()
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set({"dify_runtime": "workflow_api"})
    try:
        chunks = list(
            fake.ToolEngine.generic_invoke(
                fake.FakeTool(),
                {"q": "today news"},
                "user-1",
                types.SimpleNamespace(),
                0,
                app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
            )
        )
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert "blocked web_search" in chunks[0].message.text
    assert fake.ToolEngine.generic_calls == []
    assert guard.runtime.calls == [("tool_invoke", "before")]


def test_workflow_node_filter_skips_unmatched_app(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "other-app")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    from agentguard import AgentGuard

    created_guards = []
    monkeypatch.setattr(
        dify_adapter,
        "_make_guard",
        lambda metadata: created_guards.append(AgentGuard("unexpected", sandbox="noop")),
    )
    node = fake.DifyNodeFactory().create_node(
        {"id": "1782718941283", "data": {"type": "llm", "title": "构造联网query"}}
    )

    chunks = list(node.run())

    assert len(chunks) == 2
    assert created_guards == []


def test_legacy_agent_node_llm_and_tool_hooks_emit_events(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "ce0aa322-1f3f-4ab9-8329-3af8588c7480")
    monkeypatch.setenv("AGENTGUARD_DIFY_NODE_IDS", "1782713638856")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("legacy-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)

    results = list(fake.AgentNode()._run())

    assert len(results) == 3
    assert created_guards
    assert dify_adapter._current_guard.get() is None
    guard = created_guards[0]
    assert _event_types(guard) == ["llm_input", "llm_output", "tool_invoke", "tool_result"]
    assert guard.trace.entries[0].event.metadata["dify_runtime"] == "legacy_api"
    assert guard.trace.entries[0].event.metadata["app_id"] == "ce0aa322-1f3f-4ab9-8329-3af8588c7480"
    assert guard.trace.entries[0].event.metadata["node_id"] == "1782713638856"
    assert guard.trace.entries[1].event.payload.output == "thinking"
    assert guard.trace.entries[2].event.payload.tool_name == "web_search"
    assert guard.trace.entries[2].event.payload.arguments == {"q": "today news"}
    assert len(guard.reported_tools) == 1
    assert guard.reported_tools[0].name == "web_search"
    assert guard.reported_tools[0].required_args == ["q"]


def test_legacy_llm_tool_call_only_output_is_null(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    payload = dify_adapter._legacy_stream_output_payload(
        [
            types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="", tool_calls=[{"name": "web_search"}]),
                    usage=None,
                )
            )
        ]
    )

    from agentguard.schemas import events as ev
    from agentguard.schemas.context import RuntimeContext

    event = ev.llm_output(RuntimeContext(session_id="dify-tool-call-only"), payload)

    assert event.payload.output is None
    assert event.payload.final_output is None
    assert event.payload.to_dict() == {
        "output": None,
        "thought": None,
        "final_output": None,
    }


def test_legacy_agent_node_filter_skips_unmatched_app(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "other-app")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    from agentguard import AgentGuard

    created_guards = []
    monkeypatch.setattr(
        dify_adapter,
        "_make_guard",
        lambda metadata: created_guards.append(AgentGuard("unexpected", sandbox="noop")),
    )

    results = list(fake.AgentNode()._run())

    assert len(results) == 3
    assert created_guards == []


def test_legacy_plugin_backwards_llm_creates_guard_without_agent_node_context(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "ce0aa322-1f3f-4ab9-8329-3af8588c7480")
    monkeypatch.setenv("AGENTGUARD_DIFY_NODE_IDS", "1782713638856")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("plugin-backwards-llm-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    payload = types.SimpleNamespace(
        provider="langgenius/openai/openai",
        model="gpt-4o-mini",
        model_type="llm",
        mode="chat",
        completion_params={},
        prompt_messages=[types.SimpleNamespace(content="query")],
        tools=[types.SimpleNamespace(name="web_search")],
        stop=[],
        stream=True,
    )
    chunks = list(
        fake.PluginModelBackwardsInvocation.invoke_llm(
            "user-1",
            types.SimpleNamespace(id="tenant-1"),
            payload,
        )
    )

    assert len(chunks) == 2
    assert len(created_guards) == 1
    assert _event_types(created_guards[0]) == ["llm_input", "llm_output"]
    assert created_guards[0].trace.entries[0].event.metadata["dify_runtime"] == "legacy_plugin_backwards"
    assert created_guards[0].trace.entries[0].event.metadata["app_id"] == "ce0aa322-1f3f-4ab9-8329-3af8588c7480"


def test_legacy_plugin_backwards_tool_creates_guard_and_emits_events(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "ce0aa322-1f3f-4ab9-8329-3af8588c7480")
    monkeypatch.setenv("AGENTGUARD_DIFY_NODE_IDS", "1782713638856")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("plugin-backwards-tool-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    fake.PluginToolBackwardsInvocation.calls.clear()

    chunks = list(
        fake.PluginToolBackwardsInvocation.invoke_tool(
            tenant_id="tenant-1",
            user_id="user-1",
            tool_type=types.SimpleNamespace(value="builtin"),
            provider="local_bing_web_search",
            tool_name="web_search",
            tool_parameters={"q": "today news"},
            credential_id="cred-1",
        )
    )

    assert chunks[0].message.text == "plugin tool result:today news"
    assert fake.PluginToolBackwardsInvocation.calls == [("web_search", {"q": "today news"})]
    assert len(created_guards) == 1
    assert _event_types(created_guards[0]) == ["tool_invoke", "tool_result"]
    assert created_guards[0].trace.entries[0].event.payload.tool_name == "web_search"
    assert created_guards[0].trace.entries[0].event.payload.arguments == {"q": "today news"}
    assert created_guards[0].trace.entries[0].event.metadata["dify_runtime"] == "legacy_plugin_backwards"
    assert len(created_guards[0].reported_tools) == 1
    assert created_guards[0].reported_tools[0].name == "web_search"
    assert created_guards[0].reported_tools[0].required_args == ["q"]


def test_legacy_tool_before_deny_skips_original_tool(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    class DenyRuntime:
        def __init__(self):
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase))
            decision = (
                GuardDecision.deny("blocked web_search")
                if event.event_type.value == "tool_invoke"
                else GuardDecision.allow()
            )
            return types.SimpleNamespace(decision=decision)

    class DenyGuard:
        def __init__(self):
            self.runtime = DenyRuntime()
            self.context = types.SimpleNamespace(session_id="legacy-deny")

    guard = DenyGuard()
    fake.ToolEngine.calls.clear()
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set({"dify_runtime": "legacy_api"})
    try:
        response = fake.ToolEngine.agent_invoke(
            fake.FakeTool(),
            {"q": "today news"},
            "user-1",
            "tenant-1",
            types.SimpleNamespace(id="message-1", conversation_id="conversation-1"),
            "debugger",
            types.SimpleNamespace(),
            app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
        )
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert "blocked web_search" in response[0]
    assert response[1] == []
    assert fake.ToolEngine.calls == []
    assert guard.runtime.calls == [("tool_invoke", "before")]
