import importlib
import sys
import types

import pytest

from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.utils.errors import AdapterError


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
            yield from model.invoke_llm(
                [types.SimpleNamespace(content="query")],
                tools=[types.SimpleNamespace(name="web_search")],
                stream=True,
            )
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
            yield from model.invoke_llm(
                [types.SimpleNamespace(content=f"{self.node_data.type} query")],
                tools=None,
                stream=True,
            )

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


def _install_fake_workflow_catalog_modules(monkeypatch):
    extensions_pkg = types.ModuleType("extensions")
    ext_database_mod = types.ModuleType("extensions.ext_database")
    models_pkg = types.ModuleType("models")
    model_mod = types.ModuleType("models.model")
    workflow_mod = types.ModuleType("models.workflow")
    sqlalchemy_mod = types.ModuleType("sqlalchemy")

    class FakeColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return ("eq", self.name, other)

        def in_(self, values):
            return ("in", self.name, set(values))

    class FakeSelect:
        def __init__(self, *entities):
            self.entities = entities

        def join(self, *args, **kwargs):
            return self

        def where(self, *args, **kwargs):
            return self

    def select(*entities):
        return FakeSelect(*entities)

    class AppMode:
        WORKFLOW = types.SimpleNamespace(value="workflow")
        ADVANCED_CHAT = types.SimpleNamespace(value="advanced-chat")

    class App:
        id = FakeColumn("app_id")
        mode = FakeColumn("mode")
        workflow_id = FakeColumn("app_workflow_id")

        def __init__(self, *, id, tenant_id, mode, workflow_id):
            self.id = id
            self.tenant_id = tenant_id
            self.mode = mode
            self.workflow_id = workflow_id

    class Workflow:
        id = FakeColumn("workflow_id")
        tenant_id = FakeColumn("workflow_tenant_id")
        app_id = FakeColumn("workflow_app_id")
        version = FakeColumn("workflow_version")
        VERSION_DRAFT = "draft"

        def __init__(self, *, id, tenant_id, app_id, version, graph, type="workflow"):
            self.id = id
            self.tenant_id = tenant_id
            self.app_id = app_id
            self.version = version
            self.graph = graph
            self.type = type

        @property
        def graph_dict(self):
            return self.graph

    app = App(id="app-1", tenant_id="tenant-1", mode="workflow", workflow_id="workflow-published")
    graph = {
        "nodes": [
            {
                "id": "tool-node-1",
                "data": {
                    "type": "tool",
                    "title": "Weekday A",
                    "provider_id": "time",
                    "provider_name": "time",
                    "provider_type": "builtin",
                    "tool_name": "weekday",
                    "tool_label": "Weekday",
                    "tool_configurations": {
                        "year": None,
                        "month": {"type": "variable", "value": ["start", "month"]},
                        "day": 1,
                    },
                },
            },
            {
                "id": "agent-node-1",
                "data": {
                    "type": "agent",
                    "title": "Legacy Agent",
                    "agent_strategy_name": "function_calling",
                    "agent_parameters": {
                        "tools": {
                            "type": "constant",
                            "value": [
                                {
                                    "provider_name": "search",
                                    "type": "builtin",
                                    "tool_name": "web_search",
                                    "parameters": {"query": None, "count": 5},
                                    "extra": {"description": "Search the web"},
                                }
                            ],
                        }
                    },
                },
            },
        ]
    }
    workflow = Workflow(
        id="workflow-published",
        tenant_id="tenant-1",
        app_id="app-1",
        version="2026-07-02T00:00:00",
        graph=graph,
    )
    class FakeScalarResult:
        def __init__(self, values):
            self._values = values

        def all(self):
            return self._values

    class FakeExecuteResult(FakeScalarResult):
        pass

    class FakeSession:
        def execute(self, stmt):
            if stmt.entities == (App, Workflow):
                return FakeExecuteResult([(app, workflow)])
            return FakeExecuteResult([])

        def scalars(self, stmt):
            return FakeScalarResult([])

    ext_database_mod.db = types.SimpleNamespace(session=FakeSession())
    model_mod.App = App
    model_mod.AppMode = AppMode
    workflow_mod.Workflow = Workflow
    sqlalchemy_mod.select = select

    modules = {
        "extensions": extensions_pkg,
        "extensions.ext_database": ext_database_mod,
        "models": models_pkg,
        "models.model": model_mod,
        "models.workflow": workflow_mod,
        "sqlalchemy": sqlalchemy_mod,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(app=app, workflow=workflow)


def _fresh_adapter(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_ENABLED", raising=False)
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "false")
    import agentguard.adapters.agent.dify as dify_adapter

    return importlib.reload(dify_adapter)


def _event_types(guard) -> list[str]:
    return [entry.event.event_type.value for entry in guard.trace.entries]


def test_dify_legacy_llm_call_binds_args_and_kwargs():
    from agentguard.adapters.agent.dify_shared import DifyLegacyLLMCall

    call = DifyLegacyLLMCall.from_args_kwargs(
        (["hello"], {"temperature": 0}, ["tool"], ["stop"]),
        {"stream": False, "callbacks": ["cb"]},
    )

    assert call.prompt_messages == ["hello"]
    assert call.model_parameters == {"temperature": 0}
    assert call.tools == ["tool"]
    assert call.stop == ["stop"]
    assert call.stream is False
    assert call.callbacks == ["cb"]


def test_dify_legacy_llm_runner_before_deny_skips_execute():
    from agentguard.adapters.agent.dify_shared import (
        DifyLegacyLLMNormalizer,
        run_dify_legacy_llm_call,
    )
    from agentguard.utils.errors import AdapterError

    calls = []

    def guard_input(_model, _call, _extra_metadata):
        calls.append("before")
        return GuardDecision.deny("blocked")

    def guard_output(_model, _output, _call, _error, _extra_metadata):
        calls.append("after")
        return GuardDecision.allow()

    def execute(_args, _kwargs):
        calls.append("execute")
        return "raw"

    with pytest.raises(AdapterError, match="blocked"):
        run_dify_legacy_llm_call(
            model=object(),
            args=(["hello"],),
            kwargs={},
            arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
            execute=execute,
            guard_input=guard_input,
            guard_output=guard_output,
            blocked_value=lambda decision: decision.reason if not decision.is_allow else None,
            normalizer=DifyLegacyLLMNormalizer(),
        )

    assert calls == ["before"]


def test_dify_legacy_llm_runner_guards_generator_after_consumption():
    from agentguard.adapters.agent.dify_shared import (
        DifyLegacyLLMNormalizer,
        run_dify_legacy_llm_call,
    )

    calls = []
    outputs = []

    def guard_input(_model, _call, _extra_metadata):
        calls.append("before")
        return GuardDecision.allow()

    def guard_output(_model, output, _call, _error, _extra_metadata):
        calls.append("after")
        outputs.append(output)
        return GuardDecision.allow()

    def execute(_args, _kwargs):
        def chunks():
            yield types.SimpleNamespace(delta=types.SimpleNamespace(message=types.SimpleNamespace(content="a")))
            yield types.SimpleNamespace(delta=types.SimpleNamespace(message=types.SimpleNamespace(content="b")))

        return chunks()

    result = run_dify_legacy_llm_call(
        model=object(),
        args=(["hello"],),
        kwargs={},
        arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
        execute=execute,
        guard_input=guard_input,
        guard_output=guard_output,
        blocked_value=lambda _decision: None,
        normalizer=DifyLegacyLLMNormalizer(),
    )

    assert calls == ["before", "after"]
    assert len(list(result)) == 2
    assert outputs == [{"output": "a\nb", "final_output": "a\nb"}]


def test_dify_legacy_llm_runner_stream_modify_releases_synthetic_chunks():
    from agentguard.adapters.agent import dify_shared

    calls = []

    def guard_input(_model, _call, _extra_metadata):
        calls.append("before")
        return GuardDecision.allow()

    def guard_output(_model, _output, _call, _error, _extra_metadata):
        calls.append("after")
        return GuardDecision.modify_llm_output(
            "rewrite streamed llm output",
            processed_content='{"output": "rewritten answer"}',
        )

    def execute(_args, _kwargs):
        def chunks():
            yield types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="raw thinking", tool_calls=[]),
                    usage=None,
                )
            )
            yield types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="raw action", tool_calls=[{"name": "web_search"}]),
                    usage=None,
                )
            )

        return chunks()

    result = dify_shared.run_dify_legacy_llm_call(
        model=object(),
        args=(["hello"],),
        kwargs={},
        arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
        execute=execute,
        guard_input=guard_input,
        guard_output=guard_output,
        blocked_value=lambda _decision: None,
        normalizer=dify_shared.DifyLegacyLLMNormalizer(include_stream_tool_calls=True),
        stream_result_builder=lambda text: iter([dify_shared.build_synthetic_llm_stream_chunk(text)]),
    )

    assert calls == ["before", "after"]
    chunks = list(result)
    assert len(chunks) == 1
    assert chunks[0].delta.message.content == "rewritten answer"
    assert chunks[0].delta.message.tool_calls == []


def test_dify_legacy_llm_runner_stream_deny_releases_blocked_synthetic_chunk():
    from agentguard.adapters.agent import dify_shared

    def guard_input(_model, _call, _extra_metadata):
        return GuardDecision.allow()

    def guard_output(_model, _output, _call, _error, _extra_metadata):
        return GuardDecision.deny("blocked")

    def execute(_args, _kwargs):
        def chunks():
            yield types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="raw answer", tool_calls=[]),
                    usage=None,
                )
            )

        return chunks()

    result = dify_shared.run_dify_legacy_llm_call(
        model=object(),
        args=(["hello"],),
        kwargs={},
        arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
        execute=execute,
        guard_input=guard_input,
        guard_output=guard_output,
        blocked_value=lambda decision: decision.reason if not decision.is_allow else None,
        normalizer=dify_shared.DifyLegacyLLMNormalizer(),
        stream_result_builder=lambda text: iter([dify_shared.build_synthetic_llm_stream_chunk(text)]),
    )

    chunks = list(result)
    assert len(chunks) == 1
    assert chunks[0].delta.message.content == "blocked"


def test_install_dify_adapter_disabled_is_noop(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_ENABLED", "false")
    import agentguard.adapters.agent.dify as dify_adapter

    dify_adapter = importlib.reload(dify_adapter)
    status = dify_adapter.install_dify_adapter()

    assert status == {"enabled": False, "patched": False, "reason": "disabled"}


def test_install_dify_adapter_without_dify_is_noop(monkeypatch):
    for name in list(sys.modules):
        if name == "dify_agent" or name.startswith("dify_agent."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    dify_adapter = _fresh_adapter(monkeypatch)

    status = dify_adapter.install_dify_adapter()

    assert status["enabled"] is True
    assert status["patched"] is False
    assert status["details"]["legacy_api"]["reason"] == "legacy_import_failed"


def test_install_dify_adapter_is_idempotent(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    first = dify_adapter.install_dify_adapter()
    second = dify_adapter.install_dify_adapter()

    assert first["patched"] is True
    assert second["patched"] is False
    assert getattr(fake.DifyNodeFactory.create_node, "__agentguard_dify_patched__", False)
    assert getattr(fake.AgentNode._run, "__agentguard_dify_patched__", False)
    assert getattr(fake.ModelInstance.invoke_llm, "__agentguard_dify_patched__", False)


def test_workflow_catalog_sync_defaults_to_api_process(monkeypatch):
    import agentguard.adapters.agent.dify_shared as dify_shared

    importlib.reload(dify_shared)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setattr(dify_adapter.sys, "argv", ["celery", "-A", "app.celery"])

    assert dify_adapter.start_dify_workflow_catalog_sync() == {
        "enabled": False,
        "reason": "process_not_allowed",
    }

    monkeypatch.setattr(dify_adapter.sys, "argv", ["gunicorn", "--bind", "0.0.0.0:5001", "app:socketio_app"])
    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs.get("name"))

        def start(self):
            started.append("started")

    monkeypatch.setattr(dify_adapter.threading, "Thread", FakeThread)

    result = dify_adapter.start_dify_workflow_catalog_sync()

    assert result == {"enabled": True, "started": False, "reason": "waiting_for_app_factory"}
    assert started == []

    class FakeApp:
        def app_context(self):
            return self

    dify_adapter.register_dify_flask_app(FakeApp())
    assert started == ["agentguard-dify-workflow-catalog-sync", "started"]


def test_install_dify_adapter_patches_legacy_api(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    first = dify_adapter.install_dify_adapter()
    second = dify_adapter.install_dify_adapter()

    assert first["patched"] is True
    assert first["details"]["workflow_api"]["patched"] is True
    assert first["details"]["legacy_api"]["patched"] is True
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
    assert dify_adapter._agent_id(guard.context.metadata) == "dify-workflow:ce0aa322-1f3f-4ab9-8329-3af8588c7480"
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
    assert guard.context.agent_id == "dify-workflow:app-1"
    assert guard.context.metadata["node_execution_id"] == "node-exec-1"
    assert guard.context.metadata["node_type"] == "code"


def test_workflow_make_guard_prefers_registered_agentguard_agent_id(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    monkeypatch.setattr(
        dify_adapter,
        "_runtime_workflow_agent_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_canonical",
                "agent_identity_code": "agic_test",
                "public_key_thumbprint": "jkt_test",
            },
            "user_agent": {"bound": True},
        },
    )
    metadata = {
        "adapter": "dify",
        "dify_runtime": "workflow_api",
        "app_id": "app-1",
        "workflow_id": "workflow-1",
        "workflow_run_id": "workflow-run-1",
        "node_id": "node-1",
        "node_type": "llm",
    }

    guard = dify_adapter._make_guard(metadata)

    assert guard.context.agent_id == "ag_canonical"
    assert guard.context.metadata["agentguard_agent_id"] == "ag_canonical"
    assert guard.context.metadata["external_agent_id"] == "dify-workflow:app-1"
    assert guard.context.metadata["agent_identity_code"] == "agic_test"
    assert guard.context.metadata["agent_public_key_thumbprint"] == "jkt_test"
    assert guard.context.metadata["agentguard_user_bound"] is True


def test_workflow_make_guard_uses_dpop_runtime_session(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        dify_adapter,
        "_runtime_workflow_agent_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_workflow",
                "agent_identity_code": "agic_workflow",
            },
            "user_agent": {"bound": True},
        },
    )

    calls = []

    class FakeRuntimeAuth:
        session_id = "ags_dify_workflow"
        session_token = "runtime-token-workflow"
        canonical_user_id = "7"

        def proof(self, method, url, access_token=None):
            return "proof"

    def ensure(**kwargs):
        calls.append(kwargs)
        return FakeRuntimeAuth()

    monkeypatch.setattr(dify_adapter._runtime_auth_manager, "ensure", ensure)

    guard = dify_adapter._make_guard(
        {
            "adapter": "dify",
            "dify_runtime": "workflow_api",
            "app_id": "app-1",
            "workflow_id": "workflow-1",
            "workflow_run_id": "workflow-run-1",
            "node_execution_id": "node-exec-1",
            "user_id": "dify-user-1",
            "dify_user_email": "alice@example.com",
        }
    )

    assert calls[0]["agent_id"] == "ag_workflow"
    assert calls[0]["external_session_id"] == "workflow-run-1"
    assert calls[0]["account_email"] == "alice@example.com"
    assert calls[0]["external_user_id"] == "dify-user-1"
    assert guard.context.session_id == "ags_dify_workflow"
    assert guard.context.agent_id == "ag_workflow"
    assert guard.context.user_id == "7"
    assert guard._remote.use_dpop_auth is True
    assert guard._remote.legacy_identity_headers is False
    assert guard._auto_close_runtime_session is False


def test_workflow_runtime_auth_failure_blocks_instead_of_legacy_fallback(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        dify_adapter,
        "_runtime_workflow_agent_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_workflow",
                "agent_identity_code": "agic_workflow",
            },
            "user_agent": {"bound": True},
        },
    )

    def ensure(**kwargs):
        raise RuntimeError("dify external session is closed")

    monkeypatch.setattr(dify_adapter._runtime_auth_manager, "ensure", ensure)

    with pytest.raises(AdapterError, match="external session is closed"):
        dify_adapter._make_guard(
            {
                "adapter": "dify",
                "dify_runtime": "workflow_api",
                "app_id": "app-1",
                "workflow_id": "workflow-1",
                "workflow_run_id": "workflow-run-1",
                "user_id": "dify-user-1",
                "dify_user_email": "alice@example.com",
            }
        )


def test_workflow_metadata_reads_workflow_run_id_from_dict_graph_params(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)
    node = types.SimpleNamespace(
        node_id="node-1",
        execution_id="node-exec-1",
        node_type="llm",
        title="LLM",
        graph_init_params={
            "workflow_id": "workflow-1",
            "workflow_run_id": "workflow-run-from-dict",
        },
        run_context=types.SimpleNamespace(
            tenant_id="tenant-1",
            user_id="user-1",
            app_id="app-1",
            invoke_from="debugger",
        ),
    )
    node_factory = types.SimpleNamespace(graph_init_params=node.graph_init_params)
    node_config = {"id": "node-1", "data": {"type": "llm", "title": "LLM"}}

    metadata = dify_adapter._metadata_from_workflow_node(node, node_factory, node_config)

    assert metadata["workflow_id"] == "workflow-1"
    assert metadata["workflow_run_id"] == "workflow-run-from-dict"


def test_workflow_runtime_auth_does_not_map_node_execution_id_as_external_session(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        dify_adapter,
        "_runtime_workflow_agent_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_workflow",
                "agent_identity_code": "agic_workflow",
            },
            "user_agent": {"bound": True},
        },
    )

    calls = []

    class FakeRuntimeAuth:
        session_id = "ags_dify_internal"
        session_token = "runtime-token-workflow"
        canonical_user_id = "7"

        def proof(self, method, url, access_token=None):
            return "proof"

    def ensure(**kwargs):
        calls.append(kwargs)
        return FakeRuntimeAuth()

    monkeypatch.setattr(dify_adapter._runtime_auth_manager, "ensure", ensure)

    guard = dify_adapter._make_guard(
        {
            "adapter": "dify",
            "dify_runtime": "workflow_api",
            "app_id": "app-1",
            "workflow_id": "workflow-1",
            "node_execution_id": "node-exec-1",
            "node_id": "node-1",
            "user_id": "dify-user-1",
            "dify_user_email": "alice@example.com",
        }
    )

    assert calls[0]["external_session_id"] is None
    assert calls[0]["cache_key"].startswith("agentguard-internal:dify:workflow_api:app-1:workflow-1")
    assert "node-exec-1" not in calls[0]["cache_key"]
    assert calls[0]["metadata"]["agentguard_internal_session_key"] == calls[0]["cache_key"]
    assert "external_session_id" not in calls[0]["metadata"]
    assert guard.context.session_id == "ags_dify_internal"


def test_workflow_metadata_reads_session_ids_from_graph_runtime_state(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    class Segment:
        def __init__(self, text):
            self.text = text
            self.value = text

    class VariablePool:
        def get(self, selector):
            values = {
                ("sys", "workflow_run_id"): "workflow-run-from-state",
                ("sys", "conversation_id"): "conversation-from-state",
            }
            value = values.get(tuple(selector))
            return Segment(value) if value else None

    node = types.SimpleNamespace(
        node_id="node-1",
        node_data=types.SimpleNamespace(type="llm", title="LLM"),
        graph_init_params=types.SimpleNamespace(workflow_id="workflow-1"),
    )
    node_factory = types.SimpleNamespace(
        graph_runtime_state=types.SimpleNamespace(variable_pool=VariablePool()),
        _dify_context=types.SimpleNamespace(
            tenant_id="tenant-1",
            user_id="user-1",
            app_id="app-1",
            workflow_id="workflow-1",
            invoke_from="debugger",
        ),
    )

    metadata = dify_adapter._metadata_from_workflow_node(
        node,
        node_factory,
        {"id": "node-1", "data": {"type": "llm", "title": "LLM"}},
    )

    assert metadata["workflow_run_id"] == "workflow-run-from-state"
    assert metadata["conversation_id"] == "conversation-from-state"
    assert dify_adapter._external_session_id_from_metadata(metadata) == "conversation-from-state"


def test_workflow_external_session_prefers_conversation_over_run(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)

    first = dify_adapter._external_session_id_from_metadata(
        {
            "dify_runtime": "workflow_api",
            "conversation_id": "conversation-1",
            "workflow_run_id": "workflow-run-1",
        }
    )
    second = dify_adapter._external_session_id_from_metadata(
        {
            "dify_runtime": "workflow_api",
            "conversation_id": "conversation-1",
            "workflow_run_id": "workflow-run-2",
        }
    )

    assert first == "conversation-1"
    assert second == "conversation-1"


def test_workflow_runtime_auth_fallback_key_is_unique_per_unmapped_run(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        dify_adapter,
        "_runtime_workflow_agent_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_workflow",
                "agent_identity_code": "agic_workflow",
            },
            "user_agent": {"bound": True},
        },
    )
    run_ids = iter(["agr_run_a", "agr_run_b"])
    monkeypatch.setattr(dify_adapter, "_new_agentguard_run_id", lambda: next(run_ids))

    calls = []

    class FakeRuntimeAuth:
        session_id = "ags_dify_internal"
        session_token = "runtime-token-workflow"
        canonical_user_id = "7"

        def proof(self, method, url, access_token=None):
            return "proof"

    def ensure(**kwargs):
        calls.append(kwargs)
        return FakeRuntimeAuth()

    monkeypatch.setattr(dify_adapter._runtime_auth_manager, "ensure", ensure)
    metadata = {
        "adapter": "dify",
        "dify_runtime": "workflow_api",
        "app_id": "app-1",
        "workflow_id": "workflow-1",
        "node_execution_id": "node-exec-1",
        "node_id": "node-1",
        "user_id": "dify-user-1",
        "dify_user_email": "alice@example.com",
    }

    dify_adapter._make_guard(dict(metadata))
    dify_adapter._make_guard(dict(metadata))

    assert calls[0]["external_session_id"] is None
    assert calls[1]["external_session_id"] is None
    assert calls[0]["cache_key"] != calls[1]["cache_key"]
    assert calls[0]["cache_key"].endswith(":agr_run_a")
    assert calls[1]["cache_key"].endswith(":agr_run_b")


def test_workflow_runtime_auth_fallback_key_reuses_current_run_scope(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        dify_adapter,
        "_runtime_workflow_agent_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_workflow",
                "agent_identity_code": "agic_workflow",
            },
            "user_agent": {"bound": True},
        },
    )

    calls = []

    class FakeRuntimeAuth:
        session_id = "ags_dify_internal"
        session_token = "runtime-token-workflow"
        canonical_user_id = "7"

        def proof(self, method, url, access_token=None):
            return "proof"

    monkeypatch.setattr(
        dify_adapter._runtime_auth_manager,
        "ensure",
        lambda **kwargs: calls.append(kwargs) or FakeRuntimeAuth(),
    )
    metadata = {
        "adapter": "dify",
        "dify_runtime": "workflow_api",
        "app_id": "app-1",
        "workflow_id": "workflow-1",
        "node_id": "node-1",
        "user_id": "dify-user-1",
        "dify_user_email": "alice@example.com",
    }

    token = dify_adapter._current_run_key.set("agr_scoped")
    try:
        dify_adapter._make_guard(dict(metadata))
        dify_adapter._make_guard(dict(metadata))
    finally:
        dify_adapter._current_run_key.reset(token)

    assert calls[0]["cache_key"] == calls[1]["cache_key"]
    assert calls[0]["cache_key"].endswith(":agr_scoped")


def test_app_generate_classmethod_patch_preserves_binding(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setattr(dify_adapter, "_new_agentguard_run_id", lambda: "agr_generate")

    class Service:
        calls = []

        @classmethod
        def generate(cls, *, app_model, user):
            cls.calls.append((app_model, user, dify_adapter._current_run_key.get()))
            return "ok"

    assert dify_adapter._patch_app_generate_service(Service) is True

    result = Service.generate(app_model="app-1", user="user-1")

    assert result == "ok"
    assert Service.calls == [("app-1", "user-1", "agr_generate")]
    assert dify_adapter._current_run_key.get() is None


def test_workflow_catalog_sync_reports_published_workflow_tools(monkeypatch):
    _install_fake_workflow_catalog_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")

    synced = []
    agent_syncs = []

    class FakeRemote:
        def __init__(self, *args, **kwargs):
            self.enabled = True

        def register_session(self, context):
            raise AssertionError("catalog sync must not register runtime sessions")

        def sync_tools(self, context, tools):
            synced.append((context.to_dict(), list(tools)))
            return {"status": "ok", "tool_count": len(tools)}

        def sync_agents(self, catalog):
            agent_syncs.append(dict(catalog))
            return {"status": "ok", "deactivated_count": 0}

    monkeypatch.setattr(dify_adapter, "RemoteGuardClient", FakeRemote)
    monkeypatch.setattr(
        dify_adapter,
        "_dify_account_email_for_app",
        lambda app: "alice@example.com",
    )

    result = dify_adapter._sync_published_workflow_catalog_once()

    assert result["app_count"] == 1
    assert result["synced"] == [
        {
            "app_id": "app-1",
            "workflow_id": "workflow-published",
            "agent_id": "dify-workflow:app-1",
            "tool_count": 2,
        }
    ]
    assert synced[0][0]["agent_id"] == "dify-workflow:app-1"
    assert synced[0][0]["metadata"]["catalog_sync"] is True
    assert synced[0][0]["metadata"]["external_provider"] == "dify"
    assert synced[0][0]["metadata"]["dify_user_email"] == "alice@example.com"
    assert synced[0][0]["metadata"]["external_account_email"] == "alice@example.com"
    assert agent_syncs == [
        {
            "provider": "dify",
            "provider_instance_id": "",
            "agent_type": "workflow",
            "external_agent_ids": ["app-1"],
            "metadata": {
                "adapter": "dify",
                "dify_runtime": "workflow_api",
                "catalog_sync": True,
            },
        }
    ]
    tools = {tool["name"]: tool for tool in synced[0][1]}
    assert sorted(tools) == ["web_search", "weekday"]
    assert tools["weekday"]["input_params"] == ["year", "month"]
    assert tools["weekday"]["metadata"]["node_id"] == "tool-node-1"
    assert tools["web_search"]["metadata"]["workflow_node_kind"] == "legacy_agent"


def test_workflow_catalog_sync_skips_unchanged_tools(monkeypatch):
    fake = _install_fake_workflow_catalog_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    remote_calls = []

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def register_session(self, context):
            raise AssertionError("catalog sync must not register runtime sessions")

        def sync_tools(self, context, tools):
            remote_calls.append(("sync", context.agent_id, len(tools)))
            return {"tool_count": len(tools)}

    monkeypatch.setattr(dify_adapter, "RemoteGuardClient", FakeRemote)

    first = dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)
    second = dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)

    assert first["tool_count"] == 2
    assert second["skipped"] is True
    assert remote_calls == [
        ("sync", "dify-workflow:app-1", 2),
    ]


def test_workflow_catalog_sync_refreshes_agent_metadata_when_tools_unchanged(monkeypatch):
    fake = _install_fake_workflow_catalog_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    fake.app.name = "Workflow One"
    fake.app.description = "old"
    registrations = []
    sync_calls = []

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def register_session(self, context):
            return {"status": "ok"}

        def sync_tools(self, context, tools):
            sync_calls.append([tool["name"] for tool in tools])
            return {"tool_count": len(tools)}

    def register_agent(_remote, **kwargs):
        registrations.append((kwargs["name"], kwargs["description"]))
        return {"agent": {"agent_id": "canonical-workflow"}}

    monkeypatch.setattr(dify_adapter, "RemoteGuardClient", FakeRemote)
    monkeypatch.setattr(dify_adapter, "_register_dify_agent", register_agent)

    dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)
    fake.app.description = "new"
    second = dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)

    assert second["skipped"] is True
    assert registrations == [("Workflow One", "old"), ("Workflow One", "new")]
    assert sync_calls == [["weekday", "web_search"]]


def test_workflow_app_update_schedules_workflow_catalog_sync(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)
    scheduled = []

    monkeypatch.setattr(dify_adapter, "_schedule_published_workflow_app_catalog_sync", scheduled.append)

    dify_adapter._on_workflow_app_updated(types.SimpleNamespace(id="agent-app", mode="agent-chat"))
    dify_adapter._on_workflow_app_updated(types.SimpleNamespace(id="app-1", mode="workflow"))

    assert scheduled == ["app-1"]


def test_workflow_catalog_sync_uses_registered_agentguard_agent_id(monkeypatch):
    fake = _install_fake_workflow_catalog_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    synced = []

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            self.agent_id = kwargs.get("agent_id")

        def register_session(self, context):
            raise AssertionError("catalog sync must not register runtime sessions")

        def sync_tools(self, context, tools):
            synced.append((self.agent_id, context.to_dict(), list(tools)))
            return {"tool_count": len(tools)}

    monkeypatch.setattr(dify_adapter, "RemoteGuardClient", FakeRemote)
    monkeypatch.setattr(
        dify_adapter,
        "_register_dify_agent",
        lambda *args, **kwargs: {
            "agent": {
                "agent_id": "ag_canonical",
                "agent_identity_code": "agic_test",
                "public_key_thumbprint": "jkt_test",
            },
            "user_agent": {"bound": True},
        },
    )

    result = dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)

    assert result["agent_id"] == "ag_canonical"
    assert synced[0][0] == "ag_canonical"
    assert synced[0][1]["agent_id"] == "ag_canonical"
    assert synced[0][1]["metadata"]["external_agent_id"] == "dify-workflow:app-1"


def test_workflow_catalog_sync_keeps_agent_id_stable_across_publish_ids(monkeypatch):
    fake = _install_fake_workflow_catalog_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    synced_agent_ids = []

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def register_session(self, context):
            raise AssertionError("catalog sync must not register runtime sessions")

        def sync_tools(self, context, tools):
            synced_agent_ids.append(("sync", context.agent_id, context.metadata["workflow_id"]))
            return {"tool_count": len(tools)}

    monkeypatch.setattr(dify_adapter, "RemoteGuardClient", FakeRemote)

    first = dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)
    fake.workflow.id = "workflow-published-next"
    fake.workflow.version = "2026-07-02T01:00:00"
    second = dify_adapter._sync_workflow_tool_catalog(fake.app, fake.workflow)

    assert first["agent_id"] == "dify-workflow:app-1"
    assert second["agent_id"] == "dify-workflow:app-1"
    assert synced_agent_ids == [
        ("sync", "dify-workflow:app-1", "workflow-published"),
        ("sync", "dify-workflow:app-1", "workflow-published-next"),
    ]


def test_workflow_publish_hook_schedules_single_workflow_sync(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)
    scheduled = []

    class WorkflowService:
        def publish_workflow(self, *, session, app_model, account):
            return types.SimpleNamespace(id="workflow-1")

    monkeypatch.setattr(
        dify_adapter,
        "_schedule_published_workflow_catalog_sync",
        lambda workflow: scheduled.append(workflow.id),
    )

    assert dify_adapter._patch_workflow_publish_service(WorkflowService) is True
    service = WorkflowService()
    result = service.publish_workflow(session=None, app_model=None, account=None)

    assert result.id == "workflow-1"
    assert scheduled == ["workflow-1"]


def test_workflow_publish_sync_retries_until_published_workflow_visible(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_DIFY_PUBLISH_SYNC_DELAY_S", "0")
    monkeypatch.setenv("AGENTGUARD_DIFY_PUBLISH_SYNC_RETRIES", "3")
    calls = []
    contexts = []

    class FakeAppContext:
        def __enter__(self):
            contexts.append("enter")

        def __exit__(self, exc_type, exc, tb):
            contexts.append("exit")
            return False

    class FakeApp:
        def app_context(self):
            return FakeAppContext()

    def sync(workflow_id):
        calls.append(workflow_id)
        if len(calls) == 1:
            return {"synced": [], "reason": "workflow_not_found"}
        return {"synced": [{"workflow_id": workflow_id}]}

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon

        def start(self):
            self.target()

    monkeypatch.setattr(dify_adapter, "_dify_flask_app", lambda: FakeApp())
    monkeypatch.setattr(dify_adapter, "_sync_published_workflow_by_id_once", sync)
    monkeypatch.setattr(dify_adapter.threading, "Thread", ImmediateThread)

    dify_adapter._schedule_published_workflow_catalog_sync(types.SimpleNamespace(id="workflow-1"))

    assert calls == ["workflow-1", "workflow-1"]
    assert contexts == ["enter", "exit", "enter", "exit"]


def test_workflow_catalog_sync_filters_app_ids(monkeypatch):
    _install_fake_workflow_catalog_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "other-app")
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def register_session(self, context):
            raise AssertionError("filtered app should not register")

        def sync_tools(self, context, tools):
            raise AssertionError("filtered app should not sync")

    monkeypatch.setattr(dify_adapter, "RemoteGuardClient", FakeRemote)

    result = dify_adapter._sync_published_workflow_catalog_once()

    assert result == {"app_count": 1, "synced": []}


def test_workflow_catalog_sync_dedupes_same_tool_name_by_agent(monkeypatch):
    fake = _install_fake_workflow_catalog_modules(monkeypatch)
    fake.workflow.graph["nodes"].append(
        {
            "id": "tool-node-2",
            "data": {
                "type": "tool",
                "title": "Weekday B",
                "provider_id": "time",
                "provider_name": "time",
                "provider_type": "builtin",
                "tool_name": "weekday",
                "tool_configurations": {"timezone": None},
            },
        }
    )
    dify_adapter = _fresh_adapter(monkeypatch)

    tools = dify_adapter._workflow_catalog_tools(fake.app, fake.workflow)
    by_name = {tool["name"]: tool for tool in tools}

    assert [tool["name"] for tool in tools].count("weekday") == 1
    assert by_name["weekday"]["input_params"] == ["year", "month", "timezone"]
    assert by_name["weekday"]["metadata"]["workflow_node_ids"] == ["tool-node-1", "tool-node-2"]


def test_workflow_catalog_sync_includes_nested_legacy_agent_params(monkeypatch):
    fake = _install_fake_workflow_catalog_modules(monkeypatch)
    fake.workflow.graph["nodes"][1]["data"]["agent_parameters"]["tools"]["value"].append(
        {
            "provider_name": "enterprise",
            "type": "builtin",
            "tool_name": "queryEnterpriseMaterials",
            "parameters": {
                "company_name": {"auto": 1, "value": None},
                "credit_code": {"auto": 1, "value": None},
                "project_name": {"auto": 1, "value": None},
                "policy_id": {"auto": 1, "value": None},
                "fixed_scope": {"value": "published"},
            },
            "extra": {"description": "Query enterprise materials"},
        }
    )
    dify_adapter = _fresh_adapter(monkeypatch)

    tools = dify_adapter._workflow_catalog_tools(fake.app, fake.workflow)
    by_name = {tool["name"]: tool for tool in tools}

    assert by_name["queryEnterpriseMaterials"]["input_params"] == [
        "company_name",
        "credit_code",
        "project_name",
        "policy_id",
    ]
    assert by_name["queryEnterpriseMaterials"]["required_args"] == []
    assert by_name["queryEnterpriseMaterials"]["schema"] == {
        "type": "object",
        "properties": {
            "company_name": {},
            "credit_code": {},
            "project_name": {},
            "policy_id": {},
        },
        "required": [],
    }


def test_workflow_catalog_sync_uses_registered_app_context(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)
    calls = []

    class FakeAppContext:
        def __enter__(self):
            calls.append("enter")

        def __exit__(self, exc_type, exc, tb):
            calls.append("exit")
            return False

    class FakeApp:
        def app_context(self):
            return FakeAppContext()

    dify_adapter.register_dify_flask_app(FakeApp())
    monkeypatch.setattr(dify_adapter, "_sync_published_workflow_catalog_once", lambda: {"app_count": 0, "synced": []})

    result = dify_adapter._sync_published_workflow_catalog_with_context()

    assert result == {"app_count": 0, "synced": []}
    assert calls == ["enter", "exit"]


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


def test_workflow_tool_generator_restores_context_during_iteration(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    from agentguard import AgentGuard

    guard = AgentGuard("workflow-tool-deferred-test", sandbox="noop")
    guard.context.metadata.update({"dify_runtime": "workflow_api", "dify_user_email": "admin@example.com"})
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set(dict(guard.context.metadata))
    try:
        result = fake.ToolEngine.generic_invoke(
            fake.FakeTool(),
            {"q": "today news"},
            "user-1",
            types.SimpleNamespace(),
            0,
            conversation_id="conversation-1",
            app_id="ce0aa322-1f3f-4ab9-8329-3af8588c7480",
            message_id="message-1",
        )
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    chunks = list(result)

    assert chunks[0].message.text == "workflow tool result:today news"
    assert _event_types(guard) == ["tool_invoke", "tool_result"]
    assert guard.trace.entries[1].event.metadata["dify_user_email"] == "admin@example.com"


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


def test_workflow_legacy_llm_hook_replaces_legacy_guard_with_dpop_guard(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    class RecordingRuntime:
        def __init__(self):
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase))
            return types.SimpleNamespace(decision=GuardDecision.allow())

        def sync_local_cache_now(self, reason=""):
            self.calls.append(("flush", reason))

    class FakeGuard:
        def __init__(self, *, dpop: bool):
            self.runtime = RecordingRuntime()
            self.context = types.SimpleNamespace(session_id="legacy", agent_id="ag_workflow", user_id="7")
            self._remote = types.SimpleNamespace(
                use_dpop_auth=dpop,
                session_token="runtime-token" if dpop else None,
            )
            self.closed = False

        def close(self):
            self.closed = True

    legacy_guard = FakeGuard(dpop=False)
    dpop_guard = FakeGuard(dpop=True)
    created = []

    def make_guard(metadata):
        created.append(metadata)
        return dpop_guard

    monkeypatch.setattr(dify_adapter, "_make_guard", make_guard)
    monkeypatch.setattr(
        dify_adapter,
        "_metadata_with_registered_workflow_agent",
        lambda metadata: {**metadata, "dify_user_email": "alice@example.com", "agentguard_agent_id": "ag_workflow"},
    )
    token_guard = dify_adapter._current_guard.set(legacy_guard)
    token_meta = dify_adapter._current_metadata.set(
        {
            "adapter": "dify",
            "dify_runtime": "workflow_api",
            "app_id": "app-1",
            "workflow_id": "workflow-1",
            "user_id": "dify-user-1",
        }
    )
    try:
        chunks = list(
            fake.ModelInstance().invoke_llm(
                [types.SimpleNamespace(content="query")],
                stream=True,
            )
        )
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert len(chunks) == 2
    assert created
    assert created[0]["dify_user_email"] == "alice@example.com"
    assert legacy_guard.runtime.calls == []
    assert dpop_guard.runtime.calls[:2] == [("llm_input", "before"), ("llm_output", "after")]
    assert dpop_guard.closed is True


def test_workflow_generator_restores_context_during_iteration(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    class RecordingRuntime:
        def __init__(self):
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase, event.metadata.get("node_id")))
            return types.SimpleNamespace(decision=GuardDecision.allow())

        def sync_local_cache_now(self, reason=""):
            self.calls.append(("flush", reason, None))

    class FakeGuard:
        def __init__(self):
            self.runtime = RecordingRuntime()
            self.context = types.SimpleNamespace(session_id="ags_dify", agent_id="ag_workflow", user_id="7")
            self._remote = types.SimpleNamespace(use_dpop_auth=True, session_token="runtime-token")
            self.closed = False

        def close(self):
            self.closed = True

    guard = FakeGuard()
    monkeypatch.setattr(dify_adapter, "_make_guard", lambda metadata: guard)
    node = fake.DifyNodeFactory().create_node(
        {"id": "llm-node-1", "data": {"type": "llm", "title": "LLM"}}
    )

    generated = node.run()

    assert dify_adapter._current_guard.get() is None
    assert dify_adapter._current_metadata.get({}) == {}

    chunks = list(generated)

    assert len(chunks) == 2
    assert guard.runtime.calls[:2] == [
        ("llm_input", "before", "llm-node-1"),
        ("llm_output", "after", "llm-node-1"),
    ]
    assert guard.closed is True


def test_workflow_stream_llm_modify_output_replays_synthetic_chunk(monkeypatch):
    fake = _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)
    dify_adapter.install_dify_adapter()

    class ModifyRuntime:
        def __init__(self) -> None:
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase))
            if event.event_type.value == "llm_output":
                return types.SimpleNamespace(
                    decision=GuardDecision.modify_llm_output(
                        "rewrite streamed llm output",
                        processed_content='{"output": "rewritten workflow answer"}',
                    )
                )
            return types.SimpleNamespace(decision=GuardDecision.allow())

    guard = types.SimpleNamespace(
        runtime=ModifyRuntime(),
        context=types.SimpleNamespace(session_id="workflow-stream-rewrite", agent_id="agent"),
    )
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set(
        {
            "adapter": "dify",
            "dify_runtime": "workflow_api",
            "app_id": "app-1",
            "workflow_id": "workflow-1",
            "node_id": "node-1",
        }
    )
    try:
        result = fake.ModelInstance().invoke_llm(
            [types.SimpleNamespace(content="query")],
            stream=True,
        )
        assert guard.runtime.calls == [("llm_input", "before"), ("llm_output", "after")]
        chunks = list(result)
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert len(chunks) == 1
    assert chunks[0].delta.message.content == "rewritten workflow answer"
    assert chunks[0].delta.message.tool_calls == []


def test_workflow_llm_loopback_retries_with_aligned_thought(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)

    class ModelInstance:
        model_name = "gpt-4o-mini"
        provider = "langgenius/openai/openai"

        def __init__(self):
            self.seen_prompt_messages = []

        def invoke_llm(
            self,
            prompt_messages,
            model_parameters=None,
            tools=None,
            stop=None,
            stream=True,
            callbacks=None,
        ):
            self.seen_prompt_messages.append(prompt_messages)
            content = "first answer" if len(self.seen_prompt_messages) == 1 else "second answer"
            return types.SimpleNamespace(
                message=types.SimpleNamespace(content=content, tool_calls=[]),
                prompt_messages=prompt_messages,
            )

    dify_adapter._patch_legacy_model_invoke_llm(ModelInstance)

    class LoopbackRuntime:
        def __init__(self) -> None:
            self.events = []

        def guard(self, event, phase="before"):
            self.events.append((event.event_type.value, phase, dict(event.metadata)))
            if (
                event.event_type.value == "llm_output"
                and event.metadata.get("thought_alignment_attempt") != 1
            ):
                return types.SimpleNamespace(
                    decision=GuardDecision(
                        decision_type=DecisionType.LOOP_BACK_TO_LLM,
                        reason="retry",
                        processed_content="aligned thought",
                        metadata={"protocol": "thought_alignment_v1"},
                    )
                )
            return types.SimpleNamespace(decision=GuardDecision.allow())

    guard = types.SimpleNamespace(
        runtime=LoopbackRuntime(),
        context=types.SimpleNamespace(session_id="workflow-loopback", agent_id="agent"),
    )
    token_guard = dify_adapter._current_guard.set(guard)
    token_meta = dify_adapter._current_metadata.set(
        {
            "adapter": "dify",
            "dify_runtime": "workflow_api",
            "app_id": "app-1",
            "workflow_id": "workflow-1",
            "node_id": "node-1",
        }
    )
    try:
        model = ModelInstance()
        result = model.invoke_llm(
            [types.SimpleNamespace(content="query")],
            stream=False,
        )
    finally:
        dify_adapter._current_metadata.reset(token_meta)
        dify_adapter._current_guard.reset(token_guard)

    assert result.message.content == "second answer"
    assert len(model.seen_prompt_messages) == 2
    assert model.seen_prompt_messages[0][-1].content == "query"
    assert model.seen_prompt_messages[1][-1].content == "aligned thought"
    assert model.seen_prompt_messages[1][-1].role == "assistant"
    assert guard.runtime.events[0][2]["thought_regeneration_supported"] is True
    assert guard.runtime.events[1][2]["thought_regeneration_supported"] is True
    assert guard.runtime.events[2][2]["thought_alignment_attempt"] == 1
    assert guard.runtime.events[3][2]["thought_alignment_attempt"] == 1


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


def test_llm_output_payload_splits_structured_reasoning(monkeypatch):
    dify_adapter = _fresh_adapter(monkeypatch)

    payload = dify_adapter._llm_output_payload(
        {
            "content": "visible answer",
            "additional_kwargs": {"reasoning_content": "hidden reasoning"},
        }
    )

    assert payload == {
        "output": "visible answer",
        "final_output": "visible answer",
        "thought": "hidden reasoning",
    }


def test_legacy_stream_output_payload_splits_think_tags(monkeypatch):
    _install_fake_legacy_dify_modules(monkeypatch)
    dify_adapter = _fresh_adapter(monkeypatch)

    payload = dify_adapter._legacy_stream_output_payload(
        [
            types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="<think>hidden reasoning</think>"),
                    usage=None,
                )
            ),
            types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="<final>visible answer</final>"),
                    usage=None,
                )
            ),
        ]
    )

    assert payload == {
        "output": "<think>hidden reasoning</think>\n<final>visible answer</final>",
        "final_output": "visible answer",
        "thought": "hidden reasoning",
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
