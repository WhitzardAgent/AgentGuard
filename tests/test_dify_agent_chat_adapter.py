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


def test_llm_output_payload_splits_structured_reasoning(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)

    payload = adapter._llm_output_payload(
        {
            "content": "visible answer",
            "response_metadata": {"reasoning_content": "hidden reasoning"},
            "tool_calls": [{"name": "web_search"}],
        }
    )

    assert payload["output"] == "visible answer"
    assert payload["final_output"] == "visible answer"
    assert payload["thought"] == "hidden reasoning"
    assert payload["tool_calls"] == [{"name": "web_search"}]


def test_llm_stream_output_payload_splits_think_tags(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)

    payload = adapter._llm_stream_output_payload(
        [
            types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="<think>hidden reasoning</think>", tool_calls=None)
                )
            ),
            types.SimpleNamespace(
                delta=types.SimpleNamespace(
                    message=types.SimpleNamespace(content="\n<final>visible answer</final>", tool_calls=None)
                )
            ),
        ]
    )

    assert payload == {
        "output": "<think>hidden reasoning</think>\n<final>visible answer</final>",
        "final_output": "visible answer",
        "thought": "hidden reasoning",
    }


def _install_fake_app_event(monkeypatch):
    events_pkg = types.ModuleType("events")
    app_event_mod = types.ModuleType("events.app_event")

    class FakeSignal:
        def __init__(self):
            self.receivers = []

        def connect(self, receiver, weak=True):
            self.receivers.append((receiver, weak))
            return receiver

        def send(self, sender, **kwargs):
            for receiver, _weak in list(self.receivers):
                receiver(sender, **kwargs)

    signal = FakeSignal()
    app_event_mod.app_model_config_was_updated = signal
    monkeypatch.setitem(sys.modules, "events", events_pkg)
    monkeypatch.setitem(sys.modules, "events.app_event", app_event_mod)
    return signal


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


def test_install_dify_agent_chat_adapter_connects_config_update_event(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    signal = _install_fake_app_event(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setattr(adapter.sys, "argv", ["gunicorn", "--bind", "0.0.0.0:5001", "app:socketio_app"])
    scheduled = []

    monkeypatch.setattr(adapter, "_schedule_agent_chat_catalog_sync", scheduled.append)

    first = adapter.install_dify_agent_chat_adapter()
    second = adapter.install_dify_agent_chat_adapter()

    assert first["config_update_sync"] == {"enabled": True, "installed": True}
    assert second["config_update_sync"] == {"enabled": True, "installed": False, "reason": "already_installed"}
    assert len(signal.receivers) == 1
    assert getattr(fake.AgentChatAppRunner.run, "__agentguard_dify_agent_chat_patched__", False)

    signal.send(types.SimpleNamespace(id="app-1"))

    assert scheduled == ["app-1"]


def test_agent_chat_config_update_ignores_non_agent_mode_changes(monkeypatch):
    signal = _install_fake_app_event(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    scheduled = []

    monkeypatch.setattr(adapter, "_schedule_agent_chat_catalog_sync", scheduled.append)
    adapter.install_dify_agent_chat_config_update_sync()

    signal.send(types.SimpleNamespace(id="app-1"), app_model_config=types.SimpleNamespace(agent_mode=None))
    signal.send(types.SimpleNamespace(id="app-1"), app_model_config={"opening_statement": "hello"})
    signal.send(types.SimpleNamespace(id="app-1"), app_model_config={"agent_mode": {"enabled": True}})

    assert scheduled == ["app-1"]


def test_agent_chat_catalog_sync_defaults_to_api_process(monkeypatch):
    import agentguard.adapters.agent.dify_flask as dify_flask

    importlib.reload(dify_flask)
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_DIFY_CATALOG_SYNC_ENABLED", "true")
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setattr(adapter.sys, "argv", ["celery", "-A", "app.celery"])

    assert adapter.start_dify_agent_chat_catalog_sync() == {
        "enabled": False,
        "reason": "process_not_allowed",
    }

    monkeypatch.setattr(adapter.sys, "argv", ["gunicorn", "--bind", "0.0.0.0:5001", "app:socketio_app"])
    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            started.append(kwargs.get("name"))

        def start(self):
            started.append("started")

    monkeypatch.setattr(adapter.threading, "Thread", FakeThread)

    result = adapter.start_dify_agent_chat_catalog_sync()

    assert result == {"enabled": True, "started": False, "reason": "waiting_for_app_factory"}
    assert started == []

    class FakeApp:
        def app_context(self):
            return self

    adapter.register_dify_flask_app(FakeApp())
    assert started == ["agentguard-dify-agent-chat-catalog-sync", "started"]


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
    agent_syncs = []

    class FakeRemote:
        def __init__(self, *args, **kwargs):
            self.enabled = True

        def register_session(self, context):
            registered.append(context.to_dict())
            return {"status": "ok"}

        def sync_tools(self, context, tools):
            synced.append((context.to_dict(), list(tools)))
            return {"status": "ok", "tool_count": len(tools)}

        def sync_agents(self, catalog):
            agent_syncs.append(dict(catalog))
            return {"status": "ok", "deactivated_count": 0}

    monkeypatch.setattr(adapter, "RemoteGuardClient", FakeRemote)
    monkeypatch.setattr(adapter, "_published_agent_chat_apps", lambda: [app])
    monkeypatch.setattr(
        adapter,
        "_dify_account_email_for_app",
        lambda app: "alice@example.com",
    )

    result = adapter._sync_published_agent_catalog_once()

    assert result["app_count"] == 1
    assert registered[0]["agent_id"] == "dify-agent-chat:app-1"
    assert registered[0]["metadata"]["external_provider"] == "dify"
    assert registered[0]["metadata"]["dify_user_email"] == "alice@example.com"
    assert registered[0]["metadata"]["external_account_email"] == "alice@example.com"
    assert synced[0][0]["agent_id"] == "dify-agent-chat:app-1"
    assert synced[0][0]["metadata"]["dify_user_email"] == "alice@example.com"
    assert agent_syncs == [
        {
            "provider": "dify",
            "provider_instance_id": "",
            "agent_type": "agent_chat",
            "external_agent_ids": ["app-1"],
            "metadata": {
                "adapter": "dify_agent_chat",
                "dify_runtime": "agent_chat",
                "catalog_sync": True,
            },
        }
    ]
    assert [tool["name"] for tool in synced[0][1]] == ["weekday"]
    assert synced[0][1][0]["input_params"] == ["year", "month", "day"]


def test_agent_chat_catalog_sync_skips_unchanged_tools(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    app = types.SimpleNamespace(
        id="app-1",
        tenant_id="tenant-1",
        app_model_config_id="config-1",
        app_model_config=types.SimpleNamespace(
            agent_mode_dict={
                "enabled": True,
                "tools": [
                    {
                        "enabled": True,
                        "provider_id": "time",
                        "provider_type": "builtin",
                        "tool_name": "weekday",
                        "tool_parameters": {"year": None},
                    }
                ],
            }
        ),
    )
    remote_calls = []

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def register_session(self, context):
            remote_calls.append(("register", context.agent_id))

        def sync_tools(self, context, tools):
            remote_calls.append(("sync", context.agent_id, len(tools)))
            return {"tool_count": len(tools)}

    monkeypatch.setattr(adapter, "RemoteGuardClient", FakeRemote)

    first = adapter._sync_app_tool_catalog(app)
    second = adapter._sync_app_tool_catalog(app)

    assert first["tool_count"] == 1
    assert second["skipped"] is True
    assert remote_calls == [
        ("register", "dify-agent-chat:app-1"),
        ("sync", "dify-agent-chat:app-1", 1),
    ]


def test_agent_chat_catalog_sync_sends_updates_when_tools_change(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    agent_mode = {
        "enabled": True,
        "tools": [
            {
                "enabled": True,
                "provider_id": "time",
                "provider_type": "builtin",
                "tool_name": "weekday",
                "tool_parameters": {"year": None},
            }
        ],
    }
    app = types.SimpleNamespace(
        id="app-1",
        tenant_id="tenant-1",
        app_model_config_id="config-1",
        app_model_config=types.SimpleNamespace(agent_mode_dict=agent_mode),
    )
    synced_tools = []

    class FakeRemote:
        enabled = True

        def __init__(self, *args, **kwargs):
            pass

        def register_session(self, context):
            return {"status": "ok"}

        def sync_tools(self, context, tools):
            synced_tools.append([tool["name"] for tool in tools])
            return {"tool_count": len(tools)}

    monkeypatch.setattr(adapter, "RemoteGuardClient", FakeRemote)

    adapter._sync_app_tool_catalog(app)
    agent_mode["tools"].append(
        {
            "enabled": True,
            "provider_id": "time",
            "provider_type": "builtin",
            "tool_name": "timestamp_to_localtime",
            "tool_parameters": {"timestamp": None},
        }
    )
    adapter._sync_app_tool_catalog(app)
    agent_mode["tools"][0]["enabled"] = False
    adapter._sync_app_tool_catalog(app)

    assert synced_tools == [
        ["weekday"],
        ["weekday", "timestamp_to_localtime"],
        ["timestamp_to_localtime"],
    ]


def test_agent_chat_catalog_sync_uses_registered_app_context(monkeypatch):
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

    adapter.register_dify_flask_app(FakeApp())
    monkeypatch.setattr(adapter, "_sync_published_agent_catalog_once", lambda: {"app_count": 0, "synced": []})

    result = adapter._sync_published_agent_catalog_with_context()

    assert result == {"app_count": 0, "synced": []}
    assert calls == ["enter", "exit"]


def test_agent_chat_session_id_uses_conversation_before_message(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.delenv("AGENTGUARD_SERVER_URL", raising=False)
    metadata = {
        "app_id": "app-1",
        "conversation_id": "conversation-1",
        "message_id": "message-1",
        "task_id": "task-1",
        "user_id": "user-1",
    }

    guard = adapter._make_guard(metadata)

    assert guard.context.agent_id == "dify-agent-chat:app-1"
    assert guard.context.session_id == "conversation-1"
    assert guard.context.user_id == "user-1"
    assert guard.context.metadata["message_id"] == "message-1"
    assert guard.context.task_id == "task-1"


def test_agent_chat_make_guard_uses_dpop_runtime_session(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        adapter,
        "_runtime_agent_chat_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_agent_chat",
                "agent_identity_code": "agic_chat",
            },
            "user_agent": {"bound": True},
        },
    )

    calls = []

    class FakeRuntimeAuth:
        session_id = "ags_dify_chat"
        session_token = "runtime-token-chat"
        canonical_user_id = "7"

        def proof(self, method, url, access_token=None):
            return "proof"

    def ensure(**kwargs):
        calls.append(kwargs)
        return FakeRuntimeAuth()

    monkeypatch.setattr(adapter._runtime_auth_manager, "ensure", ensure)

    guard = adapter._make_guard(
        {
            "app_id": "app-1",
            "conversation_id": "conversation-1",
            "message_id": "message-1",
            "user_id": "dify-user-1",
            "dify_user_email": "alice@example.com",
        }
    )

    assert calls[0]["agent_id"] == "ag_agent_chat"
    assert calls[0]["external_session_id"] == "conversation-1"
    assert calls[0]["account_email"] == "alice@example.com"
    assert calls[0]["external_user_id"] == "dify-user-1"
    assert guard.context.session_id == "ags_dify_chat"
    assert guard.context.agent_id == "ag_agent_chat"
    assert guard.context.user_id == "7"
    assert guard._remote.use_dpop_auth is True
    assert guard._remote.legacy_identity_headers is False
    assert guard._auto_close_runtime_session is False


def test_agent_chat_runtime_auth_does_not_map_message_id_as_external_session(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)
    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")
    monkeypatch.setenv("AGENTGUARD_API_KEY", "sk-test")
    monkeypatch.setattr(
        adapter,
        "_runtime_agent_chat_registration",
        lambda metadata: {
            "agent": {
                "agent_id": "ag_agent_chat",
                "agent_identity_code": "agic_chat",
            },
            "user_agent": {"bound": True},
        },
    )

    calls = []

    class FakeRuntimeAuth:
        session_id = "ags_dify_chat_internal"
        session_token = "runtime-token-chat"
        canonical_user_id = "7"

        def proof(self, method, url, access_token=None):
            return "proof"

    def ensure(**kwargs):
        calls.append(kwargs)
        return FakeRuntimeAuth()

    monkeypatch.setattr(adapter._runtime_auth_manager, "ensure", ensure)

    guard = adapter._make_guard(
        {
            "app_id": "app-1",
            "message_id": "message-1",
            "user_id": "dify-user-1",
            "dify_user_email": "alice@example.com",
        }
    )

    assert calls[0]["external_session_id"] is None
    assert calls[0]["cache_key"].startswith("agentguard-internal:dify-agent-chat:app-1:dify-user-1")
    assert "message-1" not in calls[0]["cache_key"]
    assert calls[0]["metadata"]["agentguard_internal_session_key"] == calls[0]["cache_key"]
    assert "external_session_id" not in calls[0]["metadata"]
    assert guard.context.session_id == "ags_dify_chat_internal"


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



def test_agent_chat_llm_modify_input_rewrites_prompt_messages(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)

    class ModelInstance:
        def __init__(self):
            self.seen_prompt_messages = None

        def invoke_llm(
            self,
            prompt_messages,
            model_parameters=None,
            tools=None,
            stop=None,
            stream=True,
            callbacks=None,
        ):
            self.seen_prompt_messages = prompt_messages
            return types.SimpleNamespace(message=types.SimpleNamespace(content="final", tool_calls=[]))

    adapter._patch_model_invoke_llm(ModelInstance)

    class ModifyRuntime:
        def guard(self, event, phase="before"):
            if event.event_type.value == "llm_input":
                return types.SimpleNamespace(
                    decision=GuardDecision.modify_llm_input(
                        "rewrite llm input",
                        processed_content='[{"role": "user", "content": "rewritten prompt"}]',
                    )
                )
            return types.SimpleNamespace(decision=GuardDecision.allow())

    guard = types.SimpleNamespace(
        runtime=ModifyRuntime(),
        context=types.SimpleNamespace(session_id="rewrite-input", agent_id="agent"),
    )
    token_guard = adapter._current_guard.set(guard)
    token_meta = adapter._current_metadata.set({"app_id": "app-1"})
    try:
        model = ModelInstance()
        result = model.invoke_llm(
            prompt_messages=[types.SimpleNamespace(content="original prompt")],
            stream=False,
        )
    finally:
        adapter._current_metadata.reset(token_meta)
        adapter._current_guard.reset(token_guard)

    assert result.message.content == "final"
    assert model.seen_prompt_messages == [{"role": "user", "content": "rewritten prompt"}]


def test_agent_chat_llm_modify_output_rewrites_message_content(monkeypatch):
    adapter = _fresh_adapter(monkeypatch)

    class ModelInstance:
        def invoke_llm(
            self,
            prompt_messages,
            model_parameters=None,
            tools=None,
            stop=None,
            stream=True,
            callbacks=None,
        ):
            return types.SimpleNamespace(message=types.SimpleNamespace(content="original answer", tool_calls=[]))

    adapter._patch_model_invoke_llm(ModelInstance)

    class ModifyRuntime:
        def guard(self, event, phase="before"):
            if event.event_type.value == "llm_output":
                return types.SimpleNamespace(
                    decision=GuardDecision.modify_llm_output(
                        "rewrite llm output",
                        processed_content='{"output": "rewritten answer"}',
                    )
                )
            return types.SimpleNamespace(decision=GuardDecision.allow())

    guard = types.SimpleNamespace(
        runtime=ModifyRuntime(),
        context=types.SimpleNamespace(session_id="rewrite-output", agent_id="agent"),
    )
    token_guard = adapter._current_guard.set(guard)
    token_meta = adapter._current_metadata.set({"app_id": "app-1"})
    try:
        result = ModelInstance().invoke_llm(
            prompt_messages=[types.SimpleNamespace(content="original prompt")],
            stream=False,
        )
    finally:
        adapter._current_metadata.reset(token_meta)
        adapter._current_guard.reset(token_guard)

    assert result.message.content == "rewritten answer"


def test_agent_chat_tool_modify_invoke_rewrites_tool_parameters(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    fake.ToolEngine.calls = []
    adapter._patch_tool_agent_invoke(fake.ToolEngine)

    class ModifyRuntime:
        def guard(self, event, phase="before"):
            if event.event_type.value == "tool_invoke":
                return types.SimpleNamespace(
                    decision=GuardDecision.modify_tool_invoke(
                        "rewrite tool args",
                        processed_content='{"year": 2027, "month": 3, "day": 1}',
                    )
                )
            return types.SimpleNamespace(decision=GuardDecision.allow())

    tool = fake.BaseAgentRunner()._init_prompt_tools()[0]["weekday"]
    message = types.SimpleNamespace(id="message-1", conversation_id="conversation-1")
    guard = types.SimpleNamespace(
        runtime=ModifyRuntime(),
        context=types.SimpleNamespace(session_id="tool-input", agent_id="agent"),
    )
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

    assert response[0] == "Saturday"
    assert fake.ToolEngine.calls == [("weekday", {"year": 2027, "month": 3, "day": 1})]


def test_agent_chat_tool_modify_result_rewrites_tool_observation(monkeypatch):
    fake = _install_fake_dify_agent_chat_modules(monkeypatch)
    adapter = _fresh_adapter(monkeypatch)
    fake.ToolEngine.calls = []
    adapter._patch_tool_agent_invoke(fake.ToolEngine)

    class ModifyRuntime:
        def guard(self, event, phase="before"):
            if event.event_type.value == "tool_result":
                return types.SimpleNamespace(
                    decision=GuardDecision.modify_tool_result(
                        "rewrite tool result",
                        processed_content='{"result": "Sunday"}',
                    )
                )
            return types.SimpleNamespace(decision=GuardDecision.allow())

    tool = fake.BaseAgentRunner()._init_prompt_tools()[0]["weekday"]
    message = types.SimpleNamespace(id="message-1", conversation_id="conversation-1")
    guard = types.SimpleNamespace(
        runtime=ModifyRuntime(),
        context=types.SimpleNamespace(session_id="tool-output", agent_id="agent"),
    )
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

    assert response[0] == "Sunday"
    assert response[1] == []
