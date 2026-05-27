"""Tests for agentguard/api/routes.py using starlette TestClient."""
from __future__ import annotations

import json
import pytest

pytest.importorskip("fastapi", reason="requires agentguard[server]")

from fastapi.testclient import TestClient  # noqa: E402

from agentguard.api.routes import build_app  # noqa: E402
from agentguard.sdk.guard import Guard  # noqa: E402
from agentguard.tests.conftest import mini_guard, build_event as _mk  # noqa: E402


DENY_DSL = """
RULE test_deny_all
ON tool_call(*)
IF principal.role == "blocked"
THEN DENY
"""

ALLOW_DSL = """
RULE test_allow_all
ON tool_call(*)
IF principal.role == "analyst"
THEN ALLOW
"""

INVALID_DSL = """
RULE broken_rule
ON tool_call(*)
IF principal.role == "blocked"
"""

WARNING_DSL = """
RULE duplicate_rule
ON tool_call(*)
IF principal.role == "blocked"
THEN DENY

RULE duplicate_rule
ON tool_call(*)
IF principal.role == "analyst"
THEN ALLOW
"""

LLM_CHECK_DSL = """
RULE review_destructive_shell
ON tool_call(shell.exec)
IF args.cmd == "rm -rf /"
THEN LLM_CHECK
"""

LLM_CHECK_V3_PROMPT_DSL = """
RULE: review-destructive-shell
ON: tool_call(shell.exec)
CONDITION: args.cmd == "rm -rf /"
POLICY: LLM_CHECK
Prompt: "Treat destructive shell commands as high-risk. If intent is not clearly safe, escalate to human."
Severity: critical
Category: shell
Reason: "Potentially destructive shell command."
"""


class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeLLMBackend:
    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls = 0
        self.last_messages = None

    def chat(self, messages):
        self.calls += 1
        self.last_messages = messages
        return _FakeLLMResponse(self.verdict)


@pytest.fixture()
def client_no_auth():
    guard = mini_guard(DENY_DSL)
    app = build_app(guard)
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture()
def client_with_key():
    guard = mini_guard(ALLOW_DSL)
    guard._api_key = "secret-key"
    app = build_app(guard)
    return TestClient(app, raise_server_exceptions=True)


# ──────────────────────────────────────────────────────────────────────────────
# /health
# ──────────────────────────────────────────────────────────────────────────────

def test_health(client_no_auth):
    r = client_no_auth.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "rules" in body


# ──────────────────────────────────────────────────────────────────────────────
# POST /v1/evaluate
# ──────────────────────────────────────────────────────────────────────────────

def test_evaluate_allow(client_no_auth):
    ev = _mk("safe_tool", args={"x": 1})
    r = client_no_auth.post("/v1/evaluate", content=ev.model_dump_json())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["decision"]["action"] in ("allow", "deny", "human_check", "degrade")


def test_evaluate_deny(client_no_auth):
    from agentguard.models.events import Principal
    p = Principal(agent_id="x", session_id="s", role="blocked", trust_level=1)
    ev = _mk("safe_tool", principal=p)
    r = client_no_auth.post("/v1/evaluate", content=ev.model_dump_json())
    assert r.status_code == 200
    assert r.json()["decision"]["action"] == "deny"


def test_evaluate_invalid_json(client_no_auth):
    r = client_no_auth.post("/v1/evaluate", content=b"not json")
    assert r.status_code == 422


def test_evaluate_resolves_llm_check_to_final_action():
    backend = _FakeLLMBackend("deny")
    guard = Guard(policy_source=LLM_CHECK_DSL, builtin_rules=False, llm_backend=backend)

    with TestClient(build_app(guard), raise_server_exceptions=True) as client:
        first = _mk("shell.exec", args={"cmd": "ls"})
        first_r = client.post("/v1/evaluate", content=first.model_dump_json())
        assert first_r.status_code == 200

        ev = _mk("shell.exec", args={"cmd": "rm -rf /"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())

    assert r.status_code == 200
    body = r.json()
    assert body["decision"]["action"] == "deny"
    assert body["decision"]["client_action"] == "deny"
    assert backend.calls == 1
    assert backend.last_messages is not None
    assert "Trace summary:" in backend.last_messages[1]["content"]
    assert 'shell.exec(cmd="ls")' in backend.last_messages[1]["content"]
    assert 'shell.exec(cmd="rm -rf /")' not in backend.last_messages[1]["content"]
    guard.close()


def test_evaluate_only_runs_llm_review_for_matching_llm_check_rule():
    backend = _FakeLLMBackend("allow")
    guard = Guard(policy_source=LLM_CHECK_DSL, builtin_rules=False, llm_backend=backend)

    with TestClient(build_app(guard), raise_server_exceptions=True) as client:
        ev = _mk("shell.exec", args={"cmd": "ls"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())

    assert r.status_code == 200
    body = r.json()
    assert body["decision"]["action"] == "allow"
    assert backend.calls == 0
    guard.close()


def test_evaluate_uses_v3_prompt_for_remote_llm_check_system_message():
    backend = _FakeLLMBackend(
        "<DECISION>human</DECISION><REASON>Command is destructive and intent is not clearly justified.</REASON>"
    )
    guard = Guard(policy_source=LLM_CHECK_V3_PROMPT_DSL, builtin_rules=False, llm_backend=backend)

    with TestClient(build_app(guard), raise_server_exceptions=True) as client:
        ev = _mk("shell.exec", args={"cmd": "rm -rf /"})
        r = client.post("/v1/evaluate", content=ev.model_dump_json())

    assert r.status_code == 200
    assert backend.last_messages is not None
    system_prompt = backend.last_messages[0]["content"]
    assert system_prompt.startswith(
        "Treat destructive shell commands as high-risk. If intent is not clearly safe, escalate to human."
    )
    assert "<DECISION>" in system_prompt
    assert "<REASON>" in system_prompt
    assert r.json()["decision"]["reason"].startswith("llm_escalated:")
    assert "rule_reason=Potentially destructive shell command." in r.json()["decision"]["reason"]
    assert "llm_reason=Command is destructive and intent is not clearly justified." in r.json()["decision"]["reason"]
    guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# POST /v1/evaluate/batch
# ──────────────────────────────────────────────────────────────────────────────

def test_evaluate_batch(client_no_auth):
    ev = _mk("batch_tool")
    payload = json.dumps({"events": [ev.model_dump(mode="json"), ev.model_dump(mode="json")]})
    r = client_no_auth.post("/v1/evaluate/batch", content=payload,
                            headers={"content-type": "application/json"})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    assert all(res["ok"] for res in results)


def test_evaluate_batch_resolves_llm_check_to_final_action():
    backend = _FakeLLMBackend("human")
    guard = Guard(policy_source=LLM_CHECK_DSL, builtin_rules=False, llm_backend=backend)
    payload = json.dumps(
        {
            "events": [
                _mk("shell.exec", args={"cmd": "rm -rf /"}).model_dump(mode="json"),
                _mk("shell.exec", args={"cmd": "ls"}).model_dump(mode="json"),
            ]
        }
    )

    with TestClient(build_app(guard), raise_server_exceptions=True) as client:
        r = client.post(
            "/v1/evaluate/batch",
            content=payload,
            headers={"content-type": "application/json"},
        )

    assert r.status_code == 200
    results = r.json()["results"]
    assert results[0]["decision"]["action"] == "human_check"
    assert results[0]["decision"]["client_action"] == "human_check"
    assert results[1]["decision"]["action"] == "allow"
    assert backend.calls == 1
    guard.close()


# ──────────────────────────────────────────────────────────────────────────────
# Authentication
# ──────────────────────────────────────────────────────────────────────────────

def test_auth_missing_key_returns_401(client_with_key):
    ev = _mk("test")
    r = client_with_key.post("/v1/evaluate", content=ev.model_dump_json())
    assert r.status_code == 401


def test_auth_wrong_key_returns_401(client_with_key):
    ev = _mk("test")
    r = client_with_key.post(
        "/v1/evaluate", content=ev.model_dump_json(),
        headers={"x-api-key": "wrong"},
    )
    assert r.status_code == 401


def test_auth_correct_key_passes(client_with_key):
    ev = _mk("test")
    r = client_with_key.post(
        "/v1/evaluate", content=ev.model_dump_json(),
        headers={"x-api-key": "secret-key"},
    )
    assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# /rules/reload + /rules
# ──────────────────────────────────────────────────────────────────────────────

def test_reload_rules(client_no_auth):
    payload = json.dumps({"source": ALLOW_DSL})
    r = client_no_auth.post("/rules/reload", content=payload,
                            headers={"content-type": "application/json"})
    assert r.status_code == 200
    assert r.json()["loaded"] >= 1


def test_check_rules_valid_dsl_returns_report(client_no_auth):
    r = client_no_auth.post("/rules/check", json={"source": ALLOW_DSL})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["rule_count"] >= 1
    assert set(body) >= {"ok", "rule_count", "source_file", "errors", "warnings", "hints"}


def test_check_rules_invalid_dsl_returns_ok_false_with_errors(client_no_auth):
    r = client_no_auth.post("/rules/check", json={"source": INVALID_DSL})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["errors"]


def test_check_rules_returns_warnings_without_publishing(client_no_auth):
    before = client_no_auth.get("/rules")
    assert before.status_code == 200
    before_rules = before.json()

    r = client_no_auth.post("/rules/check", json={"source": WARNING_DSL})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["warnings"] or body["hints"]

    after = client_no_auth.get("/rules")
    assert after.status_code == 200
    assert after.json() == before_rules


def test_check_rules_missing_source_returns_422(client_no_auth):
    r = client_no_auth.post("/rules/check", json={})
    assert r.status_code == 422


def test_check_rules_invalid_json_returns_422(client_no_auth):
    r = client_no_auth.post("/rules/check", content=b"not json")
    assert r.status_code == 422


def test_check_rules_requires_api_key_when_enabled(client_with_key):
    r = client_with_key.post("/rules/check", json={"source": ALLOW_DSL})
    assert r.status_code == 401

    r = client_with_key.post(
        "/rules/check",
        json={"source": ALLOW_DSL},
        headers={"x-api-key": "wrong"},
    )
    assert r.status_code == 401

    r = client_with_key.post(
        "/rules/check",
        json={"source": ALLOW_DSL},
        headers={"x-api-key": "secret-key"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_list_rules(client_no_auth):
    r = client_no_auth.get("/rules")
    assert r.status_code == 200
    rules = r.json()
    assert isinstance(rules, list)
    assert rules
    assert all(rule["source"] for rule in rules)
    assert all("user_managed" in rule for rule in rules)
    assert all(rule["user_managed"] is False for rule in rules)


def test_reload_rules_marks_runtime_published_rules_as_user_managed(client_no_auth):
    payload = json.dumps({"source": ALLOW_DSL})
    r = client_no_auth.post("/rules/reload", content=payload,
                            headers={"content-type": "application/json"})
    assert r.status_code == 200

    rules = client_no_auth.get("/rules").json()
    user_rule = next(rule for rule in rules if rule["rule_id"] == "test_allow_all")
    assert user_rule["user_managed"] is True


def test_list_tools_returns_empty_catalog_by_default():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    r = client.get("/tools")
    assert r.status_code == 200
    assert r.json() == []


def test_post_tools_upserts_and_get_returns_public_shape():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    first = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": ["finance", "outbound"],
        },
        "input_params": ["to", "subject", "body", "cc"],
    }
    second = {
        "owner_agent_id": "agent-b",
        "name": "db.query",
        "labels": {
            "boundary": "internal",
            "sensitivity": "moderate",
            "integrity": "trusted",
            "tags": ["analytics"],
        },
        "input_params": ["sql", "limit"],
    }

    r = client.post("/tools", json=first)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.post("/tools", json=second)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/tools")
    assert r.status_code == 200
    assert r.json() == [first, second]


def test_post_tools_same_name_overwrites_existing_entry():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    first = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "moderate",
            "integrity": "trusted",
            "tags": ["old"],
        },
        "input_params": ["to"],
    }
    second = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": ["new"],
        },
        "input_params": ["to", "subject", "body"],
    }

    assert client.post("/tools", json=first).status_code == 200
    assert client.post("/tools", json=second).status_code == 200

    r = client.get("/tools")
    assert r.status_code == 200
    assert r.json() == [{
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": first["labels"],
        "input_params": second["input_params"],
    }]


def test_post_tools_same_name_different_agents_can_coexist():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    first = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "moderate",
            "integrity": "trusted",
            "tags": ["a"],
        },
        "input_params": ["to"],
    }
    second = {
        "owner_agent_id": "agent-b",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": ["b"],
        },
        "input_params": ["to", "subject"],
    }

    assert client.post("/tools", json=first).status_code == 200
    assert client.post("/tools", json=second).status_code == 200

    r = client.get("/tools")
    assert r.status_code == 200
    assert r.json() == [first, second]


def test_get_tools_for_agent_returns_only_that_agents_tools():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    first = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": [],
        },
        "input_params": ["to"],
    }
    second = {
        "owner_agent_id": "agent-b",
        "name": "db.query",
        "labels": {
            "boundary": "internal",
            "sensitivity": "moderate",
            "integrity": "trusted",
            "tags": [],
        },
        "input_params": ["sql"],
    }

    assert client.post("/tools", json=first).status_code == 200
    assert client.post("/tools", json=second).status_code == 200

    r = client.get("/agents/agent-b/tools")
    assert r.status_code == 200
    assert r.json() == [second]


def test_post_tools_requires_owner_agent_id():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    payload = {
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": [],
        },
        "input_params": ["to"],
    }

    r = client.post("/tools", json=payload)
    assert r.status_code == 422


def test_post_tools_requires_api_key_when_enabled(client_with_key):
    payload = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": [],
        },
        "input_params": ["to"],
    }

    r = client_with_key.post("/tools", json=payload)
    assert r.status_code == 401

    r = client_with_key.post("/tools", json=payload, headers={"x-api-key": "wrong"})
    assert r.status_code == 401

    r = client_with_key.post("/tools", json=payload, headers={"x-api-key": "secret-key"})
    assert r.status_code == 200


def test_patch_tool_labels_updates_registered_tool():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    payload = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "moderate",
            "integrity": "trusted",
            "tags": ["old"],
        },
        "input_params": ["to"],
    }

    assert client.post("/tools", json=payload).status_code == 200

    r = client.patch(
        "/agents/agent-a/tools/email.send/labels",
        json={
            "boundary": "internal",
            "sensitivity": "low",
            "integrity": "trusted",
            "tags": ["new"],
        },
    )
    assert r.status_code == 200
    assert r.json()["tool"]["labels"] == {
        "boundary": "internal",
        "sensitivity": "low",
        "integrity": "trusted",
        "tags": ["new"],
    }

    r = client.get("/agents/agent-a/tools")
    assert r.status_code == 200
    assert r.json() == [{
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "internal",
            "sensitivity": "low",
            "integrity": "trusted",
            "tags": ["new"],
        },
        "input_params": ["to"],
    }]


def test_patch_tool_labels_returns_404_for_missing_tool():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)

    r = client.patch(
        "/agents/agent-a/tools/email.send/labels",
        json={
            "boundary": "internal",
            "sensitivity": "low",
            "integrity": "trusted",
            "tags": [],
        },
    )

    assert r.status_code == 404


def test_patch_tool_labels_requires_api_key_when_enabled(client_with_key):
    payload = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": [],
        },
        "input_params": ["to"],
    }
    assert client_with_key.post("/tools", json=payload, headers={"x-api-key": "secret-key"}).status_code == 200

    patch_body = {
        "boundary": "internal",
        "sensitivity": "low",
        "integrity": "trusted",
        "tags": [],
    }
    assert client_with_key.patch("/agents/agent-a/tools/email.send/labels", json=patch_body).status_code == 401
    assert client_with_key.patch(
        "/agents/agent-a/tools/email.send/labels",
        json=patch_body,
        headers={"x-api-key": "wrong"},
    ).status_code == 401
    assert client_with_key.patch(
        "/agents/agent-a/tools/email.send/labels",
        json=patch_body,
        headers={"x-api-key": "secret-key"},
    ).status_code == 200


def test_post_tools_does_not_overwrite_existing_labels():
    client = TestClient(build_app(mini_guard()), raise_server_exceptions=True)
    original = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "moderate",
            "integrity": "trusted",
            "tags": ["original"],
        },
        "input_params": ["to"],
    }
    updated_registration = {
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "internal",
            "sensitivity": "low",
            "integrity": "trusted",
            "tags": ["registration"],
        },
        "input_params": ["to", "subject"],
    }

    assert client.post("/tools", json=original).status_code == 200
    assert client.patch(
        "/agents/agent-a/tools/email.send/labels",
        json={
            "boundary": "privileged",
            "sensitivity": "high",
            "integrity": "unfiltered",
            "tags": ["manual"],
        },
    ).status_code == 200
    assert client.post("/tools", json=updated_registration).status_code == 200

    r = client.get("/agents/agent-a/tools")
    assert r.status_code == 200
    assert r.json() == [{
        "owner_agent_id": "agent-a",
        "name": "email.send",
        "labels": {
            "boundary": "privileged",
            "sensitivity": "high",
            "integrity": "unfiltered",
            "tags": ["manual"],
        },
        "input_params": ["to", "subject"],
    }]


def test_catalog_label_updates_take_effect_on_next_evaluate():
    guard = mini_guard(
        """
        RULE deny_external_high_sensitivity
        ON tool_call.requested
        WHEN tool.boundary == "external" AND tool.sensitivity == "high"
        THEN DENY
        """
    )
    client = TestClient(build_app(guard), raise_server_exceptions=True)

    registration = {
        "owner_agent_id": "test-agent",
        "name": "email.send",
        "labels": {
            "boundary": "external",
            "sensitivity": "high",
            "integrity": "trusted",
            "tags": [],
        },
        "input_params": ["to"],
    }
    assert client.post("/tools", json=registration).status_code == 200

    event = _mk("email.send")
    first = client.post("/v1/evaluate", content=event.model_dump_json())
    assert first.status_code == 200
    assert first.json()["decision"]["action"] == "deny"

    assert client.patch(
        "/agents/test-agent/tools/email.send/labels",
        json={
            "boundary": "internal",
            "sensitivity": "low",
            "integrity": "trusted",
            "tags": [],
        },
    ).status_code == 200

    second = client.post("/v1/evaluate", content=event.model_dump_json())
    assert second.status_code == 200
    assert second.json()["decision"]["action"] == "allow"


# ──────────────────────────────────────────────────────────────────────────────
# /audit/recent
# ──────────────────────────────────────────────────────────────────────────────

def test_audit_recent(client_no_auth):
    ev = _mk("audit_tool")
    client_no_auth.post("/v1/evaluate", content=ev.model_dump_json())
    r = client_no_auth.get("/audit/recent")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_runtime_traffic_for_agent_returns_only_that_agents_entries(client_no_auth):
    from agentguard.models.events import Principal

    first = Principal(agent_id="agent-a", session_id="sess-a", role="blocked", trust_level=1)
    second = Principal(agent_id="agent-b", session_id="sess-b", role="blocked", trust_level=1)
    client_no_auth.post("/v1/evaluate", content=_mk("shell.exec", principal=first).model_dump_json())
    client_no_auth.post("/v1/evaluate", content=_mk("db.query", principal=second).model_dump_json())

    r = client_no_auth.get("/agents/agent-a/runtime/traffic")
    assert r.status_code == 200
    body = r.json()
    assert body
    assert all(item["agent"] == "agent-a" for item in body)
    assert all(item["tool"] != "db.query" for item in body)


def test_runtime_approvals_for_agent_returns_only_that_agents_tickets():
    guard = mini_guard()
    client = TestClient(build_app(guard), raise_server_exceptions=True)

    from agentguard.models.events import Principal

    bridge = guard.pipeline.enforcer.approval_bridge()
    bridge.enqueue(
        event_dump=_mk(
            "shell.exec",
            principal=Principal(agent_id="agent-a", session_id="sess-a", role="default", trust_level=1),
        ).model_dump(mode="json"),
        decision_dump={"action": "human_check", "matched_rules": ["rule-a"], "reason": "review"},
    )
    bridge.enqueue(
        event_dump=_mk(
            "db.query",
            principal=Principal(agent_id="agent-b", session_id="sess-b", role="default", trust_level=1),
        ).model_dump(mode="json"),
        decision_dump={"action": "human_check", "matched_rules": ["rule-b"], "reason": "review"},
    )

    r = client.get("/agents/agent-a/runtime/approvals")
    assert r.status_code == 200
    body = r.json()
    assert body
    assert all(item["event"]["principal"]["agent_id"] == "agent-a" for item in body)
    assert all(item["event"]["tool_call"]["tool_name"] != "db.query" for item in body)


def test_runtime_audit_recent_for_agent_returns_only_that_agents_records(client_no_auth):
    from agentguard.models.events import Principal

    first = Principal(agent_id="agent-a", session_id="sess-a", role="blocked", trust_level=1)
    second = Principal(agent_id="agent-b", session_id="sess-b", role="blocked", trust_level=1)
    client_no_auth.post("/v1/evaluate", content=_mk("shell.exec", principal=first).model_dump_json())
    client_no_auth.post("/v1/evaluate", content=_mk("db.query", principal=second).model_dump_json())

    r = client_no_auth.get("/agents/agent-a/runtime/audit/recent")
    assert r.status_code == 200
    body = r.json()
    assert body
    assert all(item["event"]["principal"]["agent_id"] == "agent-a" for item in body)
    assert all(item["event"]["tool_call"]["tool_name"] != "db.query" for item in body)


def test_audit_search_filters_by_user_id(client_no_auth):
    from agentguard.models.events import Principal

    user1 = Principal(agent_id="agent-1", session_id="sess-1", user_id="user-1")
    user2 = Principal(agent_id="agent-2", session_id="sess-2", user_id="user-2")
    client_no_auth.post("/v1/evaluate", content=_mk("audit_user_1", principal=user1).model_dump_json())
    client_no_auth.post("/v1/evaluate", content=_mk("audit_user_2", principal=user2).model_dump_json())

    r = client_no_auth.get("/audit/search", params={"user_id": "user-1"})
    assert r.status_code == 200
    body = r.json()
    assert body
    assert all(item["event"]["principal"]["user_id"] == "user-1" for item in body)


def test_audit_search_user_alias_filters_by_user_id(client_no_auth):
    from agentguard.models.events import Principal

    principal = Principal(agent_id="agent-1", session_id="sess-3", user_id="alias-user")
    client_no_auth.post("/v1/evaluate", content=_mk("audit_alias", principal=principal).model_dump_json())

    r = client_no_auth.get("/audit/search", params={"user": "alias-user"})
    assert r.status_code == 200
    assert any(item["event"]["principal"]["user_id"] == "alias-user" for item in r.json())
