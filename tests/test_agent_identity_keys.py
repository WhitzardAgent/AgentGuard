from agentguard.u_guard.agent_keys import (
    KEY_DIR_ENV,
    build_agent_registration_payload,
    stable_agent_key_id,
)


def test_stable_agent_key_id_includes_provider_identity_parts():
    first = stable_agent_key_id(
        provider="dify",
        provider_instance_id="local",
        tenant_id="tenant-1",
        external_agent_id="app-1",
        agent_type="workflow",
    )
    second = stable_agent_key_id(
        provider="dify",
        provider_instance_id="local",
        tenant_id="tenant-1",
        external_agent_id="app-2",
        agent_type="workflow",
    )

    assert first == "dify\x1flocal\x1ftenant-1\x1fapp-1\x1fworkflow"
    assert first != second


def test_build_agent_registration_payload_reuses_stable_local_key(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_DIR_ENV, str(tmp_path))

    first = build_agent_registration_payload(
        provider="dify",
        provider_instance_id="local",
        tenant_id="tenant-1",
        external_agent_id="app-1",
        agent_type="workflow",
        account_email="alice@example.com",
        name="Writing Agent",
        metadata={"adapter": "dify"},
    )
    second = build_agent_registration_payload(
        provider="dify",
        provider_instance_id="local",
        tenant_id="tenant-1",
        external_agent_id="app-1",
        agent_type="workflow",
        account_email="alice@example.com",
        name="Writing Agent",
        metadata={"adapter": "dify"},
    )
    other = build_agent_registration_payload(
        provider="dify",
        provider_instance_id="local",
        tenant_id="tenant-1",
        external_agent_id="app-2",
        agent_type="workflow",
        metadata={"adapter": "dify"},
    )

    assert first["public_key_jwk"] == second["public_key_jwk"]
    assert first["metadata"]["agent_public_key_thumbprint"] == second["metadata"]["agent_public_key_thumbprint"]
    assert first["public_key_jwk"] != other["public_key_jwk"]
    assert first["provider"] == "dify"
    assert first["external_agent_id"] == "app-1"
    assert first["account_email"] == "alice@example.com"
    assert first["metadata"]["adapter"] == "dify"
    assert len(list(tmp_path.glob("*.pem"))) == 2
