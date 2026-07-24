import sys
import threading
import types

from agentguard.adapters.agent import dify_shared


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
