from __future__ import annotations

from backend import app_state


class _DummyManager:
    def __init__(self, *, plugin_config=None):
        self.plugin_config = plugin_config


def test_get_manager_reads_server_plugin_config_env(monkeypatch):
    monkeypatch.setenv("AGENTGUARD_SERVER_PLUGIN_CONFIG", "./config/plugins.json")
    monkeypatch.delenv("AGENTGUARD_SERVER_CHECKER_CONFIG", raising=False)
    monkeypatch.delenv("AGENTGUARD_PLUGIN_CONFIG", raising=False)
    monkeypatch.setattr(app_state, "RuntimeManager", _DummyManager)
    monkeypatch.setattr(app_state, "_manager", None)

    manager = app_state.get_manager()

    assert manager.plugin_config == "./config/plugins.json"


def test_get_manager_supports_legacy_server_checker_config_env(monkeypatch):
    monkeypatch.delenv("AGENTGUARD_SERVER_PLUGIN_CONFIG", raising=False)
    monkeypatch.setenv("AGENTGUARD_SERVER_CHECKER_CONFIG", "./config/plugins.json")
    monkeypatch.delenv("AGENTGUARD_PLUGIN_CONFIG", raising=False)
    monkeypatch.setattr(app_state, "RuntimeManager", _DummyManager)
    monkeypatch.setattr(app_state, "_manager", None)

    manager = app_state.get_manager()

    assert manager.plugin_config == "./config/plugins.json"
