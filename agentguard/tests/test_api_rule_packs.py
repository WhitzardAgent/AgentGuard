"""HTTP API tests for rule pack and agent binding endpoints."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="requires agentguard[server]")

from fastapi.testclient import TestClient  # noqa: E402

from agentguard.api.routes import build_app  # noqa: E402
from agentguard.runtime.server import AgentGuardServer  # noqa: E402
from agentguard.tests.conftest import mini_guard  # noqa: E402


OFFICE_RULES = """
RULE: allow_office_email
ON: tool_call(email.send)
CONDITION: principal.role == "basic"
POLICY: ALLOW
"""

DEV_RULES = """
RULE: deny_dev_shell
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: DENY
"""

ALPHA_AGENT_RULE = """
RULE: alpha_agent_guard
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: DENY
"""

BETA_AGENT_RULE = """
RULE: beta_agent_guard
ON: tool_call(email.send)
CONDITION: args.recipient == "external@example.com"
POLICY: HUMAN_CHECK
"""

ALPHA_AGENT_RULE_TWO = """
RULE: alpha_agent_guard_two
ON: tool_call(docs.search)
CONDITION: args.query == "top secret"
POLICY: ALLOW
"""


@pytest.fixture()
def client():
    guard = mini_guard()
    app = build_app(guard)
    return TestClient(app, raise_server_exceptions=True), guard


@pytest.fixture()
def async_client():
    server = AgentGuardServer(mini_guard(), runtime_mode="async")
    app = build_app(server.guard, server=server)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, server


def test_list_default_packs(client):
    c, _ = client
    r = c.get("/rule-packs")
    assert r.status_code == 200
    pack_ids = {p["pack_id"] for p in r.json()}
    assert "__builtin__" in pack_ids
    assert "__default__" in pack_ids


def test_create_pack_and_bind_agent(client):
    c, _ = client
    r = c.post("/rule-packs", json={"pack_id": "office", "source": OFFICE_RULES})
    assert r.status_code == 200
    assert r.json()["pack"]["pack_id"] == "office"
    assert "allow_office_email" in r.json()["pack"]["rule_ids"]

    r = c.post("/agents/agent_office_001/rule-packs", json={"pack_id": "office"})
    assert r.status_code == 200

    r = c.get("/agents/agent_office_001/rule-packs")
    assert r.status_code == 200
    body = r.json()
    assert "office" in body["packs"]
    assert "allow_office_email" in body["rule_ids"]


def test_list_rules_for_agent_returns_effective_rule_details(client):
    c, _ = client
    c.post("/rule-packs", json={"pack_id": "office", "source": OFFICE_RULES})
    c.post("/agents/agent_office_001/rule-packs", json={"pack_id": "office"})

    r = c.get("/agents/agent_office_001/rules")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert any(rule["rule_id"] == "allow_office_email" for rule in body)

    r = c.get("/agents/unbound_agent/rules")
    assert r.status_code == 200
    assert all(rule["rule_id"] != "allow_office_email" for rule in r.json())


def test_unbind_and_remove_pack(client):
    c, _ = client
    c.post("/rule-packs", json={"pack_id": "office", "source": OFFICE_RULES})
    c.post("/agents/agent_x/rule-packs", json={"pack_id": "office"})

    r = c.delete("/agents/agent_x/rule-packs/office")
    assert r.status_code == 200

    r = c.delete("/rule-packs/office")
    assert r.status_code == 200

    r = c.get("/rule-packs/office")
    assert r.status_code == 404


def test_reject_builtin_modification(client):
    c, _ = client
    r = c.post("/rule-packs", json={"pack_id": "__builtin__", "source": OFFICE_RULES})
    assert r.status_code == 422
    r = c.delete("/rule-packs/__builtin__")
    assert r.status_code == 422


def test_pack_config_yaml(client, tmp_path: Path):
    c, _ = client
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "office.rules").write_text(OFFICE_RULES, encoding="utf-8")
    (rules_dir / "dev.rules").write_text(DEV_RULES, encoding="utf-8")

    cfg = tmp_path / "rule_packs.yaml"
    cfg.write_text(
        textwrap.dedent(
            """\
            packs:
              office:
                sources: [rules/office.rules]
              dev:
                sources: [rules/dev.rules]
            bindings:
              agent_office_001:
                packs: [office]
              agent_dev_001:
                packs: [dev, office]
            """
        ),
        encoding="utf-8",
    )

    r = c.post("/rule-packs/reload", json={"config_path": str(cfg)})
    assert r.status_code == 200
    body = r.json()
    assert set(body["packs"]) == {"office", "dev"}
    assert body["bindings"]["agent_dev_001"] == ["dev", "office"]

    r = c.get("/agent-bindings")
    assert r.status_code == 200
    bindings = r.json()
    assert set(bindings["agent_dev_001"]) == {"dev", "office"}


def test_async_runtime_syncs_rule_pack_changes(async_client):
    c, server = async_client
    r = c.post("/rule-packs", json={"pack_id": "office", "source": OFFICE_RULES})
    assert r.status_code == 200
    r = c.post("/agents/agent_office_001/rule-packs", json={"pack_id": "office"})
    assert r.status_code == 200
    assert server.async_runtime is not None
    assert "allow_office_email" in {
        rule.rule_id for rule in server.async_runtime.policy_actor.evaluator.rules_for_agent("agent_office_001")
    }


def test_create_agent_rule_creates_agent_pack_and_binding(client):
    c, _ = client

    r = c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] is True
    assert body["pack_id"] == "agent::agent-alpha"
    assert body["rule_id"] == "alpha_agent_guard"

    r = c.get("/agents/agent-alpha/rule-packs")
    assert r.status_code == 200
    assert "agent::agent-alpha" in r.json()["packs"]

    r = c.get("/agents/agent-alpha/rules")
    assert r.status_code == 200
    assert [rule["rule_id"] for rule in r.json()] == ["alpha_agent_guard"]


def test_create_agent_rule_preserves_builtin_rules_when_loaded():
    guard = mini_guard(load_builtin=True)
    app = build_app(guard)
    c = TestClient(app, raise_server_exceptions=True)

    before_rules = c.get("/agents/agent-alpha/rules").json()
    builtin_rule_ids = {
        rule["rule_id"]
        for rule in before_rules
        if str(rule.get("pack_id", "")).strip() == "__builtin__"
    }

    assert builtin_rule_ids

    r = c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE})
    assert r.status_code == 200

    after_rules = c.get("/agents/agent-alpha/rules").json()
    after_rule_ids = {rule["rule_id"] for rule in after_rules}

    assert "alpha_agent_guard" in after_rule_ids
    assert builtin_rule_ids.issubset(after_rule_ids)


def test_create_agent_rule_accumulates_and_isolates_per_agent(client):
    c, _ = client

    assert c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE}).status_code == 200
    assert c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE_TWO}).status_code == 200
    assert c.post("/agents/agent-beta/rules", json={"source": BETA_AGENT_RULE}).status_code == 200

    alpha_rules = c.get("/agents/agent-alpha/rules").json()
    beta_rules = c.get("/agents/agent-beta/rules").json()

    assert {rule["rule_id"] for rule in alpha_rules} == {"alpha_agent_guard", "alpha_agent_guard_two"}
    assert [rule["rule_id"] for rule in beta_rules] == ["beta_agent_guard"]


def test_create_agent_rule_rejects_duplicate_rule_id(client):
    c, _ = client

    assert c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE}).status_code == 200
    r = c.post("/agents/agent-beta/rules", json={"source": ALPHA_AGENT_RULE})

    assert r.status_code == 409


def test_create_agent_rule_rejects_multi_rule_source(client):
    c, _ = client

    r = c.post("/agents/agent-alpha/rules", json={"source": f"{ALPHA_AGENT_RULE}\n{BETA_AGENT_RULE}"})
    assert r.status_code == 422


def test_delete_agent_rule_only_removes_that_agents_rule(client):
    c, _ = client

    assert c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE}).status_code == 200
    assert c.post("/agents/agent-beta/rules", json={"source": BETA_AGENT_RULE}).status_code == 200

    r = c.delete("/agents/agent-alpha/rules/alpha_agent_guard")
    assert r.status_code == 200
    assert r.json()["pack_id"] == "agent::agent-alpha"

    alpha_rules = c.get("/agents/agent-alpha/rules").json()
    beta_rules = c.get("/agents/agent-beta/rules").json()
    assert all(rule["rule_id"] != "alpha_agent_guard" for rule in alpha_rules)
    assert [rule["rule_id"] for rule in beta_rules] == ["beta_agent_guard"]


def test_delete_agent_rule_rejects_builtin_rule():
    guard = mini_guard(load_builtin=True)
    app = build_app(guard)
    c = TestClient(app, raise_server_exceptions=True)

    builtin_rule_id = c.get("/rules").json()[0]["rule_id"]
    r = c.delete(f"/agents/agent-alpha/rules/{builtin_rule_id}")

    assert r.status_code == 422


def test_delete_agent_rule_returns_404_when_not_effective_for_agent(client):
    c, _ = client

    assert c.post("/agents/agent-alpha/rules", json={"source": ALPHA_AGENT_RULE}).status_code == 200
    r = c.delete("/agents/agent-beta/rules/alpha_agent_guard")

    assert r.status_code == 404
