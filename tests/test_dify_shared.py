import sys
import threading
import types

import pytest

from agentguard.adapters.agent import dify_shared
from agentguard.schemas.decisions import DecisionType, GuardDecision


def _runtime_spec() -> dify_shared.DifyRuntimeSpec:
    return dify_shared.DifyRuntimeSpec(
        adapter_name="dify",
        runtime_name="workflow_api",
        agent_type="workflow",
        fallback_agent_id=lambda metadata: f"fallback:{metadata.get('app_id')}",
        session_id_from_metadata=lambda metadata: str(metadata.get("workflow_run_id") or "session"),
        external_session_id_from_metadata=lambda metadata: metadata.get("conversation_id"),
        internal_session_key_from_metadata=lambda metadata, fallback_session_id: (
            f"internal:{metadata.get('app_id') or 'app'}:{fallback_session_id}"
        ),
        external_agent_id_for_app=lambda app_id: f"dify-workflow:{app_id}",
    )


def test_metadata_with_registered_agent_enriches_identity_fields():
    enriched = dify_shared.metadata_with_registered_agent(
        {"app_id": "app-1", "tenant_id": "tenant-1"},
        registration_loader=lambda metadata: {
            "agent": {
                "agent_id": "ag_canonical",
                "agent_identity_code": "agic_test",
                "public_key_thumbprint": "thumbprint_test",
            },
            "agent_identity_key_id": "key-id-1",
            "user_agent": {"bound": True},
        },
        external_agent_id_for_app=lambda app_id: f"dify-workflow:{app_id}",
        runtime_account_email=lambda metadata: "alice@example.com",
    )

    assert enriched["agentguard_agent_id"] == "ag_canonical"
    assert enriched["external_agent_id"] == "dify-workflow:app-1"
    assert enriched["agent_identity_code"] == "agic_test"
    assert enriched["agent_public_key_thumbprint"] == "thumbprint_test"
    assert enriched["agent_identity_key_id"] == "key-id-1"
    assert enriched["agentguard_user_bound"] is True
    assert enriched["external_account_email"] == "alice@example.com"
    assert enriched["dify_user_email"] == "alice@example.com"


def test_catalog_fingerprint_cache_roundtrip():
    cache: dict[str, str] = {}
    lock = threading.Lock()
    fingerprint = dify_shared.catalog_fingerprint([{"name": "weekday"}], "v1")

    assert dify_shared.catalog_fingerprint_unchanged("workflow:app-1", fingerprint, cache=cache, lock=lock) is False

    dify_shared.remember_catalog_fingerprint("workflow:app-1", fingerprint, cache=cache, lock=lock)

    assert dify_shared.catalog_fingerprint_unchanged("workflow:app-1", fingerprint, cache=cache, lock=lock) is True

    dify_shared.clear_catalog_fingerprints(cache=cache, lock=lock)

    assert dify_shared.catalog_fingerprint_unchanged("workflow:app-1", fingerprint, cache=cache, lock=lock) is False


def test_runtime_account_email_from_metadata_uses_app_info_loader():
    email = dify_shared.runtime_account_email_from_metadata(
        {"app_id": "app-1"},
        app_info_loader=lambda app_id: dify_shared.DifyAppInfo(
            app_id=app_id,
            account_email="owner@example.com",
        ),
    )

    assert email == "owner@example.com"


def test_dify_account_email_for_app_falls_back_to_database(monkeypatch):
    extensions_pkg = types.ModuleType("extensions")
    ext_database_mod = types.ModuleType("extensions.ext_database")
    models_pkg = types.ModuleType("models")
    model_mod = types.ModuleType("models.model")
    sqlalchemy_mod = types.ModuleType("sqlalchemy")

    class FakeColumn:
        def __init__(self, name):
            self.name = name

        def __eq__(self, other):
            return ("eq", self.name, other)

    class Account:
        id = FakeColumn("account_id")

        def __init__(self, email):
            self.email = email

    class FakeSelect:
        def __init__(self, entity):
            self.entity = entity

        def where(self, *_args, **_kwargs):
            return self

    class FakeSession:
        def scalar(self, stmt):
            assert stmt.entity is Account
            return Account("OWNER@EXAMPLE.COM")

    def select(entity):
        return FakeSelect(entity)

    ext_database_mod.db = types.SimpleNamespace(session=FakeSession())
    model_mod.Account = Account
    sqlalchemy_mod.select = select

    monkeypatch.setitem(sys.modules, "extensions", extensions_pkg)
    monkeypatch.setitem(sys.modules, "extensions.ext_database", ext_database_mod)
    monkeypatch.setitem(sys.modules, "models", models_pkg)
    monkeypatch.setitem(sys.modules, "models.model", model_mod)
    monkeypatch.setitem(sys.modules, "sqlalchemy", sqlalchemy_mod)

    app = types.SimpleNamespace(updated_by="user-1", created_by=None)

    assert dify_shared.dify_account_email_for_app(app) == "owner@example.com"


def test_runtime_agent_registration_caches_by_runtime_identity(monkeypatch):
    calls = []

    class FakeRemote:
        def __init__(self, *_args, **_kwargs):
            self.enabled = True

    def register_agent(_remote, **kwargs):
        calls.append(kwargs["agent_id"])
        return {"agent": {"agent_id": "canonical-agent"}}

    monkeypatch.setenv("AGENTGUARD_SERVER_URL", "http://agentguard.test")

    cache: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    lock = threading.Lock()
    spec = _runtime_spec()
    app_info = dify_shared.DifyAppInfo(
        app_id="app-1",
        tenant_id="tenant-1",
        name="Workflow One",
        account_email="alice@example.com",
    )
    metadata = {
        "adapter": "dify",
        "dify_runtime": "workflow_api",
        "client_session_key": "session-key-1",
    }

    first = dify_shared.runtime_agent_registration(
        metadata,
        spec=spec,
        app_info=app_info,
        registration_metadata=metadata,
        remote_client_cls=FakeRemote,
        register_agent_fn=register_agent,
        registration_cache=cache,
        registration_lock=lock,
    )
    second = dify_shared.runtime_agent_registration(
        metadata,
        spec=spec,
        app_info=app_info,
        registration_metadata=metadata,
        remote_client_cls=FakeRemote,
        register_agent_fn=register_agent,
        registration_cache=cache,
        registration_lock=lock,
    )

    assert first == second
    assert calls == ["dify-workflow:app-1"]


def test_supports_dify_thought_loopback_accepts_dict_and_object_messages():
    assert dify_shared.supports_dify_thought_loopback(
        [{"role": "user", "content": "hello"}]
    ) is True
    assert dify_shared.supports_dify_thought_loopback(
        [types.SimpleNamespace(content="hello")]
    ) is True
    assert dify_shared.supports_dify_thought_loopback("hello") is False
    assert dify_shared.supports_dify_thought_loopback([]) is False


def test_build_dify_thought_loopback_prompt_messages_preserves_native_object_type():
    original = [types.SimpleNamespace(content="original prompt", role="user", marker="keep")]
    output_template = types.SimpleNamespace(
        content="original answer",
        role="assistant",
        tool_calls=[],
        marker="output",
    )

    rebuilt = dify_shared.build_dify_thought_loopback_prompt_messages(
        original,
        "aligned thought",
        output_template=output_template,
    )

    assert rebuilt is not None
    assert len(rebuilt) == 2
    assert isinstance(rebuilt[-1], types.SimpleNamespace)
    assert rebuilt[-1].content == "aligned thought"
    assert rebuilt[-1].role == "assistant"
    assert rebuilt[-1].tool_calls == []
    assert rebuilt[-1].marker == "output"
    assert original[-1].content == "original prompt"


def test_build_dify_thought_loopback_prompt_messages_normalizes_none_tool_calls():
    original = [
        types.SimpleNamespace(
            content="older prompt",
            role="assistant",
            tool_calls=None,
            marker="existing",
        ),
        types.SimpleNamespace(
            content="original prompt",
            role="assistant",
            tool_calls=None,
            marker="keep",
        )
    ]
    output_template = types.SimpleNamespace(
        content="original answer",
        role="assistant",
        tool_calls=None,
        marker="output",
    )

    rebuilt = dify_shared.build_dify_thought_loopback_prompt_messages(
        original,
        "aligned thought",
        output_template=output_template,
    )

    assert rebuilt is not None
    assert rebuilt[0].tool_calls == []
    assert rebuilt[0].marker == "existing"
    assert rebuilt[1].tool_calls == []
    assert rebuilt[1].marker == "keep"
    assert rebuilt[-1].content == "aligned thought"
    assert rebuilt[-1].role == "assistant"
    assert rebuilt[-1].tool_calls == []
    assert rebuilt[-1].marker == "output"
    assert original[0].tool_calls is None
    assert original[-1].tool_calls is None


def test_build_synthetic_llm_stream_chunk_preserves_template_type():
    class Message:
        def __init__(self, content: str, tool_calls: list[object] | None = None) -> None:
            self.content = content
            self.tool_calls = list(tool_calls or [])

    class Delta:
        def __init__(self, message: Message) -> None:
            self.message = message
            self.usage = {"tokens": 3}

    class Chunk:
        def __init__(self, content: str, tool_calls: list[object] | None = None) -> None:
            self.delta = Delta(Message(content, tool_calls))

    original = Chunk("", [{"name": "web_search"}])

    rewritten = dify_shared.build_synthetic_llm_stream_chunk(
        "rewritten answer",
        chunk_template=original,
    )

    assert isinstance(rewritten, Chunk)
    assert rewritten.delta.message.content == "rewritten answer"
    assert rewritten.delta.message.tool_calls == []
    assert original.delta.message.content == ""
    assert original.delta.message.tool_calls == [{"name": "web_search"}]


def test_run_dify_legacy_llm_call_stream_modify_reuses_original_chunk_type():
    class Message:
        def __init__(self, content: str, tool_calls: list[object] | None = None) -> None:
            self.content = content
            self.tool_calls = list(tool_calls or [])

    class Delta:
        def __init__(self, message: Message) -> None:
            self.message = message
            self.usage = None

    class Chunk:
        def __init__(self, content: str, tool_calls: list[object] | None = None) -> None:
            self.delta = Delta(Message(content, tool_calls))

    def guard_input(_model, _call, _extra_metadata):
        return GuardDecision.allow()

    def guard_output(_model, _output, _call, _error, _extra_metadata):
        return GuardDecision.modify_llm_output(
            "rewrite streamed llm output",
            processed_content="rewritten answer",
        )

    def execute(_args, _kwargs):
        def chunks():
            yield Chunk("", [{"name": "web_search"}])

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
    )

    chunks = list(result)

    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].delta.message.content == "rewritten answer"
    assert chunks[0].delta.message.tool_calls == []


def test_loopback_metadata_from_decision_marks_thought_alignment_retry():
    decision = GuardDecision(
        decision_type=DecisionType.LOOP_BACK_TO_LLM,
        reason="retry",
        processed_content="aligned thought",
        metadata={"protocol": "thought_alignment_v1"},
    )

    assert dify_shared.loopback_metadata_from_decision(decision) == {
        "thought_alignment_attempt": 1
    }


def test_run_dify_legacy_llm_call_retries_loopback_non_stream():
    outputs = iter(["first result", "second result"])
    prompt_messages_seen = []
    metadata_seen = []

    def guard_input(_model, call, extra_metadata):
        prompt_messages_seen.append(call.prompt_messages)
        metadata_seen.append(extra_metadata)
        return GuardDecision.allow()

    def guard_output(_model, output, _call, _error, extra_metadata):
        metadata_seen.append(extra_metadata)
        if output == "first result":
            return GuardDecision(
                decision_type=DecisionType.LOOP_BACK_TO_LLM,
                reason="retry",
                processed_content="aligned thought",
                metadata={"protocol": "thought_alignment_v1"},
            )
        return GuardDecision.allow()

    result = dify_shared.run_dify_legacy_llm_call(
        model=object(),
        args=([types.SimpleNamespace(content="original prompt")],),
        kwargs={"stream": False},
        arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
        execute=lambda current_args, _current_kwargs: next(outputs),
        guard_input=guard_input,
        guard_output=guard_output,
        blocked_value=lambda _decision: None,
        normalizer=dify_shared.DifyLegacyLLMNormalizer(),
    )

    assert result == "second result"
    assert len(prompt_messages_seen) == 2
    assert prompt_messages_seen[0][-1].content == "original prompt"
    assert prompt_messages_seen[1][-1].content == "aligned thought"
    assert prompt_messages_seen[1][-1].role == "assistant"
    assert metadata_seen == [
        None,
        None,
        {"thought_alignment_attempt": 1},
        {"thought_alignment_attempt": 1},
    ]


def test_run_dify_legacy_llm_call_retries_loopback_stream():
    executions = []

    def guard_input(_model, _call, _extra_metadata):
        return GuardDecision.allow()

    def guard_output(_model, output, _call, _error, extra_metadata):
        if extra_metadata is None:
            return GuardDecision(
                decision_type=DecisionType.LOOP_BACK_TO_LLM,
                reason="retry",
                processed_content="aligned thought",
                metadata={"protocol": "thought_alignment_v1"},
            )
        return GuardDecision.allow()

    def execute(current_args, _current_kwargs):
        prompt_messages = current_args[0]
        executions.append(prompt_messages)
        if len(executions) == 1:
            text = "first thought"
        else:
            text = "second answer"

        def chunks():
            yield dify_shared.build_synthetic_llm_stream_chunk(text)

        return chunks()

    result = dify_shared.run_dify_legacy_llm_call(
        model=object(),
        args=([types.SimpleNamespace(content="original prompt")],),
        kwargs={"stream": True},
        arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
        execute=execute,
        guard_input=guard_input,
        guard_output=guard_output,
        blocked_value=lambda _decision: None,
        normalizer=dify_shared.DifyLegacyLLMNormalizer(),
    )

    chunks = list(result)

    assert len(executions) == 2
    assert executions[0][-1].content == "original prompt"
    assert executions[1][-1].content == "aligned thought"
    assert executions[1][-1].role == "assistant"
    assert len(chunks) == 1
    assert chunks[0].delta.message.content == "second answer"


def test_run_dify_legacy_llm_call_raises_when_loopback_prompt_messages_cannot_be_rebuilt():
    decision = GuardDecision(
        decision_type=DecisionType.LOOP_BACK_TO_LLM,
        reason="retry",
        processed_content="aligned thought",
    )

    with pytest.raises(Exception, match="could not rebuild prompt messages"):
        dify_shared.loopback_dify_llm_call_args_kwargs(
            decision=decision,
            call=dify_shared.DifyLegacyLLMCall(prompt_messages="opaque prompt"),
            arg_names=("prompt_messages", "model_parameters", "tools", "stop", "stream", "callbacks"),
            args=("opaque prompt",),
            kwargs={},
        )
