import importlib
import sys
import types

from agentguard.schemas.decisions import GuardDecision


def _install_fake_dify_agent_chat_modules(monkeypatch):
    core = types.ModuleType("core")
    app_pkg = types.ModuleType("core.app")
    apps_pkg = types.ModuleType("core.app.apps")
    agent_chat_pkg = types.ModuleType("core.app.apps.agent_chat")
    app_runner_mod = types.ModuleType("core.app.apps.agent_chat.app_runner")
    agent_pkg = types.ModuleType("core.agent")
    base_agent_runner_mod = types.ModuleType("core.agent.base_agent_runner")
    model_manager_mod = types.ModuleType("core.model_manager")
    tools_pkg = types.ModuleType("core.tools")
    tool_engine_mod = types.ModuleType("core.tools.tool_engine")
    tool_entities_pkg = types.ModuleType("core.tools.entities")
    tool_entities_mod = types.ModuleType("core.tools.entities.tool_entities")
    config_pkg = types.ModuleType("core.app.app_config")
    easy_pkg = types.ModuleType("core.app.app_config.easy_ui_based_app")
    easy_agent_pkg = types.ModuleType("core.app.app_config.easy_ui_based_app.agent")
    agent_manager_mod = types.ModuleType("core.app.app_config.easy_ui_based_app.agent.manager")

    class ToolInvokeMeta:
        def __init__(self, error=None, tool_config=None):
            self.error = error
            self.tool_config = tool_config or {}

        @classmethod
        def error_instance(cls, error):
            return cls(error=error)

        def to_dict(self):
            return {"error": self.error, "tool_config": self.tool_config}

    class FakeToolDescription:
        llm = "Calculate weekday."

    class FakeToolIdentity:
        def __init__(self, name, provider):
            self.name = name
            self.provider = provider
            self.icon = "icon"

    class FakeToolEntity:
        def __init__(self, name, provider):
            self.identity = FakeToolIdentity(name, provider)
            self.description = FakeToolDescription()

    class ProviderType:
        value = "builtin"

    class FakeTool:
        def __init__(self, name, provider):
            self.entity = FakeToolEntity(name, provider)
            self.invocations = []

        def tool_provider_type(self):
            return ProviderType()

        def get_llm_parameters_json_schema(self):
            return {
                "type": "object",
                "properties": {
                    "year": {"type": "integer"},
                    "month": {"type": "integer"},
                    "day": {"type": "integer"},
                },
                "required": ["year", "month", "day"],
            }

    class AgentConfigManager:
        @classmethod
        def convert(cls, config):
            enabled = []
            for tool in config.get("agent_mode", {}).get("tools", []):
                if not tool.get("enabled"):
                    continue
                enabled.append(types.SimpleNamespace(tool_name=tool["tool_name"]))
            return types.SimpleNamespace(tools=enabled)

    class BaseAgentRunner:
        def __init__(self):
            self.enabled_tool = FakeTool("weekday", "time")

        def _init_prompt_tools(self):
            return {
                "weekday": self.enabled_tool,
            }, [
                types.SimpleNamespace(name="weekday"),
            ]

    class ModelInstance:
        model_name = "deepseek-v4-flash"
        provider = "langgenius/deepseek/deepseek"

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
                            message=types.SimpleNamespace(
                                content="",
                                tool_calls=[
                                    types.SimpleNamespace(
                                        id="call-1",
                                        function=types.SimpleNamespace(
                                            name="weekday",
                                            arguments='{"year": 2026, "month": 2, "day": 28}',
                                        ),
                                    )
                                ],
                            ),
                            usage=None,
                        )
                    )

                return chunks()
            return types.SimpleNamespace(message=types.SimpleNamespace(content="final", tool_calls=[]))

    class ToolEngine:
        calls = []

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
            return "Saturday", [], ToolInvokeMeta(tool_config={"tool_name": tool.entity.identity.name})

    class AgentChatAppRunner:
        def run(self, application_generate_entity, queue_manager, conversation, message):
            base_runner = BaseAgentRunner()
            tool_instances, prompt_tools = base_runner._init_prompt_tools()
            chunks = ModelInstance().invoke_llm(
                prompt_messages=[types.SimpleNamespace(content="2026年2月28日是星期几")],
                model_parameters={},
                tools=prompt_tools,
                stop=[],
                stream=True,
                callbacks=[],
            )
            for _chunk in chunks:
                pass
            return ToolEngine.agent_invoke(
                tool=tool_instances["weekday"],
                tool_parameters={"year": 2026, "month": 2, "day": 28},
                user_id=application_generate_entity.user_id,
                tenant_id=application_generate_entity.app_config.tenant_id,
                message=message,
                invoke_from=application_generate_entity.invoke_from,
                agent_tool_callback=object(),
                conversation_id=conversation.id,
                app_id=application_generate_entity.app_config.app_id,
                message_id=message.id,
            )

    app_runner_mod.AgentChatAppRunner = AgentChatAppRunner
    base_agent_runner_mod.BaseAgentRunner = BaseAgentRunner
    model_manager_mod.ModelInstance = ModelInstance
    tool_engine_mod.ToolEngine = ToolEngine
    tool_entities_mod.ToolInvokeMeta = ToolInvokeMeta
    agent_manager_mod.AgentConfigManager = AgentConfigManager

    modules = {
        "core": core,
        "core.app": app_pkg,
        "core.app.apps": apps_pkg,
        "core.app.apps.agent_chat": agent_chat_pkg,
        "core.app.apps.agent_chat.app_runner": app_runner_mod,
        "core.agent": agent_pkg,
        "core.agent.base_agent_runner": base_agent_runner_mod,
        "core.model_manager": model_manager_mod,
        "core.tools": tools_pkg,
        "core.tools.tool_engine": tool_engine_mod,
        "core.tools.entities": tool_entities_pkg,
        "core.tools.entities.tool_entities": tool_entities_mod,
        "core.app.app_config": config_pkg,
        "core.app.app_config.easy_ui_based_app": easy_pkg,
        "core.app.app_config.easy_ui_based_app.agent": easy_agent_pkg,
        "core.app.app_config.easy_ui_based_app.agent.manager": agent_manager_mod,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return types.SimpleNamespace(
        AgentChatAppRunner=AgentChatAppRunner,
        BaseAgentRunner=BaseAgentRunner,
        AgentConfigManager=AgentConfigManager,
        ToolEngine=ToolEngine,
    )


def _fresh_adapter(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_DIFY_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "false")
    import agentguard.adapters.agent.dify_agent_chat as adapter

    return importlib.reload(adapter)


def _event_types(guard):
    return [entry.event.event_type.value for entry in guard.trace.entries]


def test_install_dify_agent_chat_adapter_disabled_is_noop(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_DIFY_AGENT_CHAT_ENABLED", raising=False)
    monkeypatch.delenv("AGENTGUARD_ENABLED", raising=False)
    import agentguard.adapters.agent.dify_agent_chat as adapter

    adapter = importlib.reload(adapter)

    assert adapter.install_dify_agent_chat_adapter() == {
        "enabled": False,
        "patched": False,
        "reason": "disabled",
    }


def test_install_dify_agent_chat_adapter_is_idempotent(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)

    first = adapter.install_dify_agent_chat_adapter()
    second = adapter.install_dify_agent_chat_adapter()

    assert first["patched"] is True
    assert second["patched"] is False
    assert getattr(fake.AgentChatAppRunner.run, "__agentguard_dify_agent_chat_patched__", False)


def test_agent_chat_catalog_sync_reports_enabled_tools(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")

    app_config = types.SimpleNamespace(
        agent_mode_dict={
            "enabled": True,
            "tools": [
                {
                    "enabled": False,
                    "provider_id": "yahoo",
                    "provider_type": "builtin",
                    "tool_name": "yahoo_finance_news",
                    "tool_parameters": {"symbol": ""},
                },
                {
                    "enabled": True,
                    "provider_id": "time",
                    "provider_type": "builtin",
                    "tool_name": "weekday",
                    "tool_label": "星期几计算器",
                    "tool_parameters": {"year": None, "month": None, "day": None},
                },
            ],
        }
    )
    app = types.SimpleNamespace(
        id="app-1",
        tenant_id="tenant-1",
        app_model_config_id="config-1",
        app_model_config=app_config,
        created_by="user-1",
        updated_by="user-1",
    )

    registered = []
    synced = []

    class FakeRemote:
        def __init__(self, *args, **kwargs):
            self.enabled = True

        def register_session(self, context):
            registered.append(context.to_dict())
            return {"status": "ok"}

        def sync_tools(self, context, tools):
            synced.append((context.to_dict(), list(tools)))
            return {"status": "ok", "tool_count": len(tools)}

    monkeypatch.setattr(adapter, "RemoteGuardClient", FakeRemote)
    monkeypatch.setattr(adapter, "_published_agent_chat_apps", lambda: [app])

    result = adapter._sync_published_agent_catalog_once()

    assert result["app_count"] == 1
    assert registered[0]["agent_id"] == "dify-agent-chat:app-1"
    assert synced[0][0]["agent_id"] == "dify-agent-chat:app-1"
    assert [tool["name"] for tool in synced[0][1]] == ["weekday"]
    assert synced[0][1][0]["input_params"] == ["year", "month", "day"]


def test_agent_chat_catalog_sync_creates_app_context_when_needed(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
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

    monkeypatch.setattr(adapter, "_dify_flask_app", lambda: FakeApp())
    monkeypatch.setattr(adapter, "_sync_published_agent_catalog_once", lambda: {"app_count": 0, "synced": []})

    result = adapter._sync_published_agent_catalog_with_context()

    assert result == {"app_count": 0, "synced": []}
    assert calls == ["enter", "exit"]


def test_agent_chat_registers_only_runtime_enabled_tools_and_emits_events(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    adapter.install_dify_agent_chat_adapter()
    created_guards = []

    from agentguard import AgentGuard

    def make_guard(metadata):
        guard = AgentGuard("dify-agent-chat-test", sandbox="noop")
        guard.context.metadata.update(metadata)
        guard.reported_tools = []
        guard._report_tool_metadata = guard.reported_tools.append
        created_guards.append(guard)
        return guard

    monkeypatch.setattr(adapter, "_make_guard", make_guard)

    raw_config = {
        "agent_mode": {
            "tools": [
                {"enabled": False, "tool_name": "yahoo_finance_analytics"},
                {"enabled": False, "tool_name": "yahoo_finance_news"},
                {"enabled": False, "tool_name": "yahoo_finance_ticker"},
                {"enabled": True, "tool_name": "weekday"},
            ]
        }
    }
    converted = fake.AgentConfigManager.convert(raw_config)
    assert [tool.tool_name for tool in converted.tools] == ["weekday"]

    app_config = types.SimpleNamespace(
        tenant_id="tenant-1",
        app_id="6680db75-b1ed-4735-b4b4-a76efe1b7b42",
        agent=types.SimpleNamespace(strategy="function_call"),
    )
    application_generate_entity = types.SimpleNamespace(
        app_config=app_config,
        user_id="user-1",
        task_id="task-1",
        invoke_from="debugger",
    )
    conversation = types.SimpleNamespace(id="conversation-1")
    message = types.SimpleNamespace(id="message-1", conversation_id="conversation-1")

    result = fake.AgentChatAppRunner().run(
        application_generate_entity=application_generate_entity,
        queue_manager=object(),
        conversation=conversation,
        message=message,
    )

    guard = created_guards[0]
    assert result[0] == "Saturday"
    assert [tool.name for tool in guard.reported_tools] == ["weekday"]
    assert "yahoo_finance_news" not in [tool.name for tool in guard.reported_tools]
    assert guard.reported_tools[0].required_args == ["year", "month", "day"]
    assert _event_types(guard) == ["llm_input", "llm_output", "tool_invoke", "tool_result"]
    assert guard.trace.entries[0].event.metadata["tool_names"] == ["weekday"]
    assert guard.trace.entries[2].event.payload.tool_name == "weekday"
    assert guard.trace.entries[2].event.payload.arguments == {"year": 2026, "month": 2, "day": 28}


def test_agent_chat_app_id_filter_skips_unmatched_app(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_APP_IDS", "other-app")
    adapter = _fresh_adapter(monkeypatch)
    adapter.install_dify_agent_chat_adapter()

    created_guards = []

    def make_guard(metadata):
        created_guards.append(metadata)
        raise AssertionError("unmatched app should not create an AgentGuard session")

    monkeypatch.setattr(adapter, "_make_guard", make_guard)

    app_config = types.SimpleNamespace(
        tenant_id="tenant-1",
        app_id="app-1",
        agent=types.SimpleNamespace(strategy="function_call"),
    )
    application_generate_entity = types.SimpleNamespace(
        app_config=app_config,
        user_id="user-1",
        task_id="task-1",
        invoke_from="debugger",
    )
    conversation = types.SimpleNamespace(id="conversation-1")
    message = types.SimpleNamespace(id="message-1", conversation_id="conversation-1")

    result = fake.AgentChatAppRunner().run(
        application_generate_entity=application_generate_entity,
        queue_manager=object(),
        conversation=conversation,
        message=message,
    )

    assert result[0] == "Saturday"
    assert created_guards == []


def test_agent_chat_tool_before_deny_returns_dify_compatible_tuple(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    adapter.install_dify_agent_chat_adapter()

    class DenyRuntime:
        def __init__(self):
            self.calls = []

        def guard(self, event, phase="before"):
            self.calls.append((event.event_type.value, phase))
            if event.event_type.value == "tool_invoke":
                decision = GuardDecision.deny("weekday blocked")
            else:
                decision = GuardDecision.allow()
            return types.SimpleNamespace(decision=decision)

    class DenyGuard:
        def __init__(self):
            self.runtime = DenyRuntime()
            self.context = types.SimpleNamespace(session_id="deny", agent_id="agent")

    tool = fake.BaseAgentRunner()._init_prompt_tools()[0]["weekday"]
    message = types.SimpleNamespace(id="message-1", conversation_id="conversation-1")
    guard = DenyGuard()
    token_guard = adapter._current_guard.set(guard)
    token_meta = adapter._current_metadata.set({"app_id": "app-1"})
    try:
        response = fake.ToolEngine.agent_invoke(
            tool=tool,
            tool_parameters={"year": 2026, "month": 2, "day": 28},
            user_id="user-1",
            tenant_id="tenant-1",
            message=message,
            invoke_from="debugger",
            agent_tool_callback=object(),
            conversation_id="conversation-1",
            app_id="app-1",
            message_id="message-1",
        )
    finally:
        adapter._current_metadata.reset(token_meta)
        adapter._current_guard.reset(token_guard)

    assert isinstance(response, tuple)
    assert response[1] == []
    assert "weekday blocked" in response[0]
    assert guard.runtime.calls == [("tool_invoke", "before")]


def test_agent_chat_tool_result_sanitize_returns_safe_observation(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    adapter.install_dify_agent_chat_adapter()

    class SanitizeRuntime:
        def guard(self, event, phase="before"):
            if event.event_type.value == "tool_result":
                decision = GuardDecision.sanitize("hide result")
            else:
                decision = GuardDecision.allow()
            return types.SimpleNamespace(decision=decision)

    class SanitizeGuard:
        def __init__(self):
            self.runtime = SanitizeRuntime()
            self.context = types.SimpleNamespace(session_id="sanitize", agent_id="agent")

    tool = fake.BaseAgentRunner()._init_prompt_tools()[0]["weekday"]
    message = types.SimpleNamespace(id="message-1", conversation_id="conversation-1")
    guard = SanitizeGuard()
    token_guard = adapter._current_guard.set(guard)
    token_meta = adapter._current_metadata.set({"app_id": "app-1"})
    try:
        response = fake.ToolEngine.agent_invoke(
            tool=tool,
            tool_parameters={"year": 2026, "month": 2, "day": 28},
            user_id="user-1",
            tenant_id="tenant-1",
            message=message,
            invoke_from="debugger",
            agent_tool_callback=object(),
            conversation_id="conversation-1",
            app_id="app-1",
            message_id="message-1",
        )
    finally:
        adapter._current_metadata.reset(token_meta)
        adapter._current_guard.reset(token_guard)

    assert isinstance(response, tuple)
    assert response[1] == []
    assert "sanitized" in response[0]
    assert "hide result" in response[0]
