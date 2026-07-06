from __future__ import annotations

from shared.schemas import events as ev
from shared.schemas.context import RuntimeContext
from shared.schemas.decisions import DecisionType

from backend.runtime.manager import RuntimeManager
from backend.runtime.plugins.llm_after.qwen3guard import Qwen3GuardOutputPlugin
from backend.runtime.plugins.llm_before.qwen3guard import Qwen3GuardInputPlugin
from backend.runtime.plugins.manager import PluginManager


def _ctx() -> RuntimeContext:
    return RuntimeContext(session_id="qwen3guard-test")


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
        Qwen3GuardInputPlugin,
        "Safety: Safe\nCategories: None",
    )

    event = ev.llm_input(_ctx(), [{"role": "user", "content": "How do I bake bread?"}])
    result = plugin.check(event, _ctx())

    assert result.decision_candidate is None
    assert result.risk_signals == []
    assert result.metadata["qwen3guard"]["safety"] == "safe"


def test_qwen3guard_input_denies_unsafe_content():
    plugin = _configured_plugin(
        Qwen3GuardInputPlugin,
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
        Qwen3GuardInputPlugin,
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


def test_qwen3guard_output_sends_assistant_message():
    captured: list = []
    plugin = _configured_plugin(
        Qwen3GuardOutputPlugin,
        "Safety: Unsafe\nCategories: Violent",
        captured,
    )

    event = ev.llm_output(_ctx(), "dangerous model output")
    result = plugin.check(event, _ctx())

    assert captured == [[{"role": "assistant", "content": "dangerous model output"}]]
    assert result.decision_candidate is not None
    assert result.decision_candidate.decision_type == DecisionType.DENY


def test_qwen3guard_plugins_load_from_server_config():
    manager = PluginManager(
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

    assert isinstance(manager.plugins_by_phase["llm_before"][0], Qwen3GuardInputPlugin)
    assert isinstance(manager.plugins_by_phase["llm_after"][0], Qwen3GuardOutputPlugin)


def test_qwen3guard_runtime_result_does_not_nest_plugin_outcomes(monkeypatch):
    monkeypatch.setattr(
        Qwen3GuardInputPlugin,
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
