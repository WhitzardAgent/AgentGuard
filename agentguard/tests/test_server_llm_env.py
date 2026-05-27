import os
from pathlib import Path
import tomllib

import agentguard.__main__ as agentguard_cli
from agentguard.runtime.server import AgentGuardServer


ROOT = Path(__file__).resolve().parents[2]
LLM_ENV_KEYS = (
    "AGENTGUARD_LLM_API_KEY",
    "AGENTGUARD_LLM_MODEL",
    "AGENTGUARD_LLM_BASE_URL",
    "AGENTGUARD_LLM_BACKEND",
    "AGENTGUARD_LLM_TRACE_MAX_STEPS",
)


class _FakeGuard:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._api_key = ""
        self.pipeline = type(
            "_Pipeline",
            (),
            {
                "handle_attempt": staticmethod(
                    lambda _event: type(
                        "_Decision",
                        (),
                        {"model_dump": staticmethod(lambda mode="json": {"action": "allow"})},
                    )()
                )
            },
        )()

    def close(self):
        return None


def test_from_policy_always_uses_env_llm_backend(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(
        "agentguard.storage.session_store.build_state_cache",
        lambda _url: sentinel,
    )
    monkeypatch.setattr("agentguard.sdk.guard.Guard", _FakeGuard)

    server = AgentGuardServer.from_policy(
        policy_source=None,
        builtin_rules=False,
        api_key="runtime-secret",
    )

    assert isinstance(server.guard, _FakeGuard)
    assert server.guard.kwargs["state_cache"] is sentinel
    assert server.guard.kwargs["llm_backend"] == "env"
    assert server.guard._api_key == "runtime-secret"


def test_local_env_loader_sets_missing_values_without_overriding(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "AGENTGUARD_LLM_BACKEND=openai",
                "AGENTGUARD_LLM_MODEL=gpt-5-nano",
                "AGENTGUARD_LLM_BASE_URL=https://api.example.test/v1",
                "AGENTGUARD_LLM_API_KEY=test-key",
                "AGENTGUARD_LLM_TRACE_MAX_STEPS=3",
            ]
        ),
        encoding="utf-8",
    )
    for key in LLM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENTGUARD_LLM_MODEL", "preset-model")

    agentguard_cli._load_local_env_file(env_path)

    assert os.environ["AGENTGUARD_LLM_BACKEND"] == "openai"
    assert os.environ["AGENTGUARD_LLM_MODEL"] == "preset-model"
    assert os.environ["AGENTGUARD_LLM_BASE_URL"] == "https://api.example.test/v1"
    assert os.environ["AGENTGUARD_LLM_API_KEY"] == "test-key"
    assert os.environ["AGENTGUARD_LLM_TRACE_MAX_STEPS"] == "3"


def test_main_global_env_file_option_loads_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("AGENTGUARD_LLM_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.delenv("AGENTGUARD_LLM_API_KEY", raising=False)

    called: dict[str, str | None] = {}

    def fake_health(ns):
        called["env"] = os.environ.get("AGENTGUARD_LLM_API_KEY")
        return 0

    monkeypatch.setattr(agentguard_cli, "_cmd_health", fake_health)

    rc = agentguard_cli.main([
        "--env-file",
        str(env_path),
        "health",
        "--url",
        "http://localhost:38080",
    ])

    assert rc == 0
    assert called["env"] == "test-key"


def test_local_eval_uses_env_llm_backend(tmp_path, monkeypatch, capsys):
    event_path = tmp_path / "event.json"
    event_path.write_text("{}", encoding="utf-8")
    observed: dict[str, object] = {}

    def _make_guard(**kwargs):
        observed.update(kwargs)
        return _FakeGuard(**kwargs)

    monkeypatch.setattr("agentguard.sdk.guard.Guard", _make_guard)

    class _FakeRuntimeEvent:
        @staticmethod
        def model_validate(payload):
            assert payload == {}
            return {"payload": payload}

    monkeypatch.setattr("agentguard.models.events.RuntimeEvent", _FakeRuntimeEvent)

    exit_code = agentguard_cli._cmd_eval(
        type(
            "_Args",
            (),
            {
                "event": str(event_path),
                "url": None,
                "api_key": "",
                "timeout": 10.0,
                "policy": ["rules/my_policy.rules"],
                "no_builtin": False,
                "mode": "enforce",
            },
        )()
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert observed["llm_backend"] == "env"
    assert '"ok": true' in out.lower()


def test_env_example_documents_llm_check_env_vars():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")

    for key in LLM_ENV_KEYS:
        assert f"{key}=" in text
    assert "AGENTGUARD_LLM_BACKEND=openai" in text
    assert "AGENTGUARD_LLM_TRACE_MAX_STEPS=5" in text


def test_docker_compose_forwards_llm_check_env_vars():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "AGENTGUARD_LLM_BACKEND: ${AGENTGUARD_LLM_BACKEND:-}" in text
    assert "AGENTGUARD_LLM_MODEL: ${AGENTGUARD_LLM_MODEL:-}" in text
    assert "AGENTGUARD_LLM_BASE_URL: ${AGENTGUARD_LLM_BASE_URL:-}" in text
    assert "AGENTGUARD_LLM_API_KEY: ${AGENTGUARD_LLM_API_KEY:-}" in text
    assert "AGENTGUARD_LLM_TRACE_MAX_STEPS: ${AGENTGUARD_LLM_TRACE_MAX_STEPS:-5}" in text


def test_server_extra_includes_openai_dependency():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server_extras = pyproject["project"]["optional-dependencies"]["server"]

    assert any(dep.startswith("openai>=") for dep in server_extras)
