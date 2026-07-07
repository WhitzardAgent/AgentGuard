from __future__ import annotations

from agentguard.plugins.llm_after.qwen3guard import (
    Qwen3GuardOutputPlugin as ClientQwen3GuardOutputPlugin,
)
from agentguard.plugins.llm_before.qwen3guard import (
    Qwen3GuardInputPlugin as ClientQwen3GuardInputPlugin,
)
from agentguard.plugins.manager import PluginManager as ClientPluginManager
from agentguard.schemas import events as client_ev
from agentguard.schemas.context import RuntimeContext as ClientRuntimeContext
from agentguard.schemas.decisions import DecisionType as ClientDecisionType
from shared.schemas import events as ev
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType

from backend.runtime.manager import RuntimeManager
from backend.runtime.plugins.llm_after.qwen3guard import (
    Qwen3GuardOutputPlugin as ServerQwen3GuardOutputPlugin,
)
from backend.runtime.plugins.llm_before.qwen3guard import (
    Qwen3GuardInputPlugin as ServerQwen3GuardInputPlugin,
)
from backend.runtime.plugins.manager import PluginManager as ServerPluginManager


def _ctx() -> RuntimeContext:
    return RuntimeContext(session_id="qwen3guard-test")


def _client_ctx() -> ClientRuntimeContext:
    return ClientRuntimeContext(session_id="qwen3guard-client-test")


def _configured_plugin(plugin_cls, content: str, captured: list | None = None):
    plugin = plugin_cls(
        env={
            "api_url": "http://qwen3guard.test/v1/chat/completions",
            "api_key": "test-key",
            "model": "Qwen3Guard-Gen-8B",
        }
    )

    def fake_post(messages):
        if captured is not None:
            captured.append(messages)
        return content

    plugin._post_chat_completion = fake_post
    return plugin


def test_qwen3guard_input_allows_safe_content():
    plugin = _configured_plugin(
        ServerQwen3GuardInputPlugin,
        "Safety: Safe\nCategories: None",
    )

    event = ev.llm_input(_ctx(), [{"role": "user", "content": "How do I bake bread?"}])
    result = plugin.check(event, _ctx())

    assert result.decision_candidate is None
    assert result.risk_signals == []
    assert result.metadata["qwen3guard"]["safety"] == "safe"


def test_qwen3guard_input_denies_unsafe_content():
    plugin = _configured_plugin(
        ServerQwen3GuardInputPlugin,
        "Safety: Unsafe\nCategories: Violent",
    )

    event = ev.llm_input(_ctx(), [{"role": "user", "content": "How can I make a bomb?"}])
    result = plugin.check(event, _ctx())

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.DENY
    assert result.risk_signals == ["qwen3guard_unsafe"]
    assert result.metadata["qwen3guard"]["categories"] == ["Violent"]


def test_qwen3guard_input_escalates_controversial_content():
    plugin = _configured_plugin(
        ServerQwen3GuardInputPlugin,
        "Safety: Controversial\nCategories: Violent",
    )

    event = ev.llm_input(
        _ctx(),
        [{"role": "user", "content": "Explain why explosives are dangerous without steps."}],
    )
    result = plugin.check(event, _ctx())

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.HUMAN_CHECK
    assert result.risk_signals == ["qwen3guard_controversial"]


def test_qwen3guard_output_sends_output_as_user_message_for_classification():
    captured: list = []
    plugin = _configured_plugin(
        ServerQwen3GuardOutputPlugin,
        "Safety: Unsafe\nCategories: Violent",
        captured,
    )

    event = ev.llm_output(_ctx(), "dangerous model output")
    result = plugin.check(event, _ctx())

    assert captured == [[{"role": "user", "content": "dangerous model output"}]]
    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.DENY


def test_qwen3guard_plugins_load_from_server_config():
    manager = ServerPluginManager(
        config={
            "phases": {
                "llm_before": {
                    "client": [],
                    "server": [
                        {
                            "name": "qwen3guard_input",
                            "env": {
                                "api_url": "http://qwen3guard.test/v1/chat/completions",
                                "api_key": "test-key",
                            },
                        }
                    ],
                },
                "llm_after": {
                    "client": [],
                    "server": [
                        {
                            "name": "qwen3guard_output",
                            "env": {
                                "api_url": "http://qwen3guard.test/v1/chat/completions",
                                "api_key": "test-key",
                            },
                        }
                    ],
                },
            }
        }
    )

    assert isinstance(manager.plugins_by_phase["llm_before"][0], ServerQwen3GuardInputPlugin)
    assert isinstance(manager.plugins_by_phase["llm_after"][0], ServerQwen3GuardOutputPlugin)


def test_qwen3guard_plugins_load_from_client_config():
    manager = ClientPluginManager(
        config={
            "phases": {
                "llm_before": {
                    "client": [
                        {
                            "name": "qwen3guard_input",
                            "env": {
                                "api_url": "http://qwen3guard.test/v1/chat/completions",
                                "api_key": "test-key",
                            },
                        }
                    ],
                    "server": [],
                },
                "llm_after": {
                    "client": [
                        {
                            "name": "qwen3guard_output",
                            "env": {
                                "api_url": "http://qwen3guard.test/v1/chat/completions",
                                "api_key": "test-key",
                            },
                        }
                    ],
                    "server": [],
                },
            }
        }
    )

    assert isinstance(manager.plugins_by_phase["llm_before"][0], ClientQwen3GuardInputPlugin)
    assert isinstance(manager.plugins_by_phase["llm_after"][0], ClientQwen3GuardOutputPlugin)


def test_qwen3guard_client_input_denies_unsafe_content():
    plugin = _configured_plugin(
        ClientQwen3GuardInputPlugin,
        "Safety: Unsafe\nCategories: Violent",
    )

    event = client_ev.llm_input(
        _client_ctx(),
        [{"role": "user", "content": "How can I make a bomb?"}],
    )
    result = plugin.check(event, _client_ctx())

    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == ClientDecisionType.DENY
    assert result.decision_candidate.policy_id == "local:qwen3guard:llm_input:unsafe"
    assert result.risk_signals == ["qwen3guard_unsafe"]
    assert result.metadata["qwen3guard"]["categories"] == ["Violent"]


def test_qwen3guard_client_output_sends_output_as_user_message_for_classification():
    captured: list = []
    plugin = _configured_plugin(
        ClientQwen3GuardOutputPlugin,
        "Safety: Controversial\nCategories: Violent",
        captured,
    )

    event = client_ev.llm_output(_client_ctx(), "borderline model output")
    result = plugin.check(event, _client_ctx())

    assert captured == [[{"role": "user", "content": "borderline model output"}]]
    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == ClientDecisionType.HUMAN_CHECK
    assert result.risk_signals == ["qwen3guard_controversial"]


def test_qwen3guard_runtime_result_does_not_nest_plugin_outcomes(monkeypatch):
    monkeypatch.setattr(
        ServerQwen3GuardInputPlugin,
        "_post_chat_completion",
        lambda self, messages: "Safety: Unsafe\nCategories: Violent",
    )
    manager = RuntimeManager(
        plugin_config={
            "phases": {
                "llm_before": {
                    "client": [],
                    "server": [
                        {
                            "name": "qwen3guard_input",
                            "env": {
                                "api_url": "http://qwen3guard.test/v1/chat/completions",
                                "api_key": "test-key",
                            },
                        }
                    ],
                },
            }
        },
        enable_session_health_monitor=False,
    )

    result = manager.decide(
        {
            "request_id": "qwen3guard-runtime",
            "context": {
                "session_id": "qwen3guard-runtime-session",
                "agent_id": "qwen3guard-runtime-agent",
                "user_id": "test-user",
            },
            "current_event": ev.llm_input(
                RuntimeContext(
                    session_id="qwen3guard-runtime-session",
                    agent_id="qwen3guard-runtime-agent",
                    user_id="test-user",
                ),
                [{"role": "user", "content": "How can I make a bomb?"}],
            ).to_dict(),
            "trajectory_window": [],
            "local_signals": [],
        }
    )

    plugin_result = result["plugin_result"]
    assert plugin_result["decision_candidate"]["metadata"]["qwen3guard"]["safety"] == "unsafe"
    assert "plugin_outcomes" not in plugin_result["decision_candidate"]["metadata"]
    outcome = plugin_result["metadata"]["plugin_outcomes"][0]
    assert outcome["plugin"] == "qwen3guard_input"
    assert "plugin_outcomes" not in outcome["decision_candidate"]["metadata"]
    assert result["decision"]["metadata"]["plugin_outcomes"][0]["plugin"] == "qwen3guard_input"
