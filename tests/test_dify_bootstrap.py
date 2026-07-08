import importlib
import sys


def test_dify_app_factory_capture_registers_create_app_result(monkeypatch, tmp_path):
    app_factory = tmp_path / "app_factory.py"
    app_factory.write_text(
        "\n".join(
            [
                "class FakeApp:",
                "    def app_context(self):",
                "        return self",
                "",
                "def create_app():",
                "    return object(), FakeApp()",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "app_factory", raising=False)

    import agentguard.adapters.agent.dify_bootstrap as bootstrap
    import agentguard.adapters.agent.dify_flask as dify_flask

    bootstrap = importlib.reload(bootstrap)
    dify_flask = importlib.reload(dify_flask)

    status = bootstrap.install_dify_app_factory_capture()

    assert status == {"installed": True, "patched": False, "reason": "import_hook_installed"}

    module = importlib.import_module("app_factory")
    result = module.create_app()

    assert isinstance(result, tuple)
    assert dify_flask.get_dify_flask_app() is result[1]


def test_dify_app_factory_capture_notifies_app_ready_callbacks(monkeypatch, tmp_path):
    app_factory = tmp_path / "app_factory.py"
    app_factory.write_text(
        "\n".join(
            [
                "class FakeApp:",
                "    def app_context(self):",
                "        return self",
                "",
                "def create_app():",
                "    return object(), FakeApp()",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "app_factory", raising=False)

    import agentguard.adapters.agent.dify_bootstrap as bootstrap
    import agentguard.adapters.agent.dify_flask as dify_flask

    bootstrap = importlib.reload(bootstrap)
    dify_flask = importlib.reload(dify_flask)
    seen = []

    dify_flask.on_dify_flask_app_ready(lambda app: seen.append(app))
    bootstrap.install_dify_app_factory_capture()

    module = importlib.import_module("app_factory")
    result = module.create_app()

    assert seen == [result[1]]


def test_dify_post_gevent_bootstrap_compat_entrypoint(monkeypatch):
    import agentguard.adapters.agent.dify_bootstrap as bootstrap

    bootstrap = importlib.reload(bootstrap)
    calls = []

    def fake_app_factory_capture():
        calls.append("app_factory")
        return {"installed": True}

    def fake_agent_chat():
        calls.append("agent_chat")
        return {"patched": True}

    def fake_workflow():
        calls.append("workflow")
        return {"patched": True}

    monkeypatch.setattr(bootstrap, "install_dify_app_factory_capture", fake_app_factory_capture)
    monkeypatch.setattr(
        "agentguard.adapters.agent.dify_agent_chat.install_dify_agent_chat_adapter",
        fake_agent_chat,
    )
    monkeypatch.setattr(
        "agentguard.adapters.agent.dify.install_dify_adapter",
        fake_workflow,
    )

    status = bootstrap.install_dify_post_gevent_bootstrap()

    assert calls == ["app_factory", "agent_chat", "workflow"]
    assert status == {
        "app_factory": {"installed": True},
        "agent_chat": {"patched": True},
        "workflow": {"patched": True},
    }
