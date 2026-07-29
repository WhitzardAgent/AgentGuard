"""Tests for the management-console state and DSL bridge (offline)."""
from __future__ import annotations

from backend.console.dsl import parse_source, policy_rule_to_source
from backend.console.state import ConsoleState
from backend.runtime.manager import RuntimeManager
from backend.runtime.plugins.base import BasePlugin, CheckResult

from shared.schemas.decisions import GuardDecision
from shared.schemas.events import EventType, RuntimeContext, llm_input, tool_event
from shared.schemas.policy import PolicyEffect, PolicyRule, RuleCondition

_DENY_RULE = (
    "RULE: block_shell\n"
    "PHASES: tool_before\n"
    "ON: tool_call(shell.exec)\n"
    'CONDITION: A.name == "shell.exec"\n'
    "POLICY: DENY\n"
    'Reason: "no shell"'
)
_LLM_RULE = (
    "RULE: review_sql\n"
    "PHASES: tool_before\n"
    "ON: tool_call(database_query)\n"
    'CONDITION: tool.sql MATCHES ".*password.*"\n'
    "POLICY: LLM_CHECK\n"
    'Prompt: "Decide allow or deny based on sensitivity."\n'
    'Reason: "review SQL"'
)
_NO_CONDITION_RULE = (
    "RULE: allow_safe_read\n"
    "PHASES: tool_before\n"
    "ON: tool_call(read_file)\n"
    "POLICY: ALLOW\n"
    'Reason: "safe read allowed"'
)


def _console() -> ConsoleState:
    return ConsoleState(
        RuntimeManager(
            plugin_config={
                "phases": {
                    "tool_before": {"client": [], "server": ["tool_invoke", "rule_based_plugin"]}
                }
            }
        )
    )


def _seed_runtime_rules(con: ConsoleState) -> None:
    con.manager.policy.store.set_rules(
        [
            PolicyRule(
                rule_id="deny_secret_exfiltration",
                effect=PolicyEffect.DENY,
                reason="Secret exfiltration detected via external send.",
                priority=100,
                event_types=["tool_invoke"],
                capabilities=["external_send"],
                conditions=[
                    RuleCondition(field="trace.contains_signal", op="eq", value="secret_detected")
                ],
            ),
            PolicyRule(
                rule_id="approve_payment",
                effect=PolicyEffect.REQUIRE_APPROVAL,
                reason="Payment actions require approval.",
                priority=80,
                event_types=["tool_invoke"],
                capabilities=["payment"],
            ),
        ]
    )


def test_dsl_parse_and_roundtrip():
    parsed, report = parse_source(_DENY_RULE)
    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.rule_id == "block_shell"
    assert rule.tool_names == ["shell.exec"]
    source = policy_rule_to_source(rule)
    assert "RULE: block_shell" in source
    assert "POLICY: DENY" in source


def test_check_reports_missing_lines():
    result = _console().check("RULE: x\nCONDITION: tool.name == \"read_file\"")
    assert result["ok"] is False
    assert any("POLICY" in e["message"] for e in result["errors"])


def test_dsl_parse_allows_rule_without_condition():
    parsed, report = parse_source(_NO_CONDITION_RULE)

    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.conditions == []
    source = policy_rule_to_source(rule)
    assert "CONDITION:" not in source


def test_dsl_parse_preserves_multiline_and_boolean_condition_expression():
    parsed, report = parse_source(
        "RULE: review_high_sensitivity\n"
        "PHASES: tool_before\n"
        "TRACE: A -> ... -> C\n"
        'CONDITION: (A.sensitivity == "high"\n'
        "  OR principal.trust_level < 2)\n"
        "POLICY: DENY\n"
    )

    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.condition_expr == '(A.sensitivity == "high"\nOR principal.trust_level < 2)'
    assert any(cond.field == "A.sensitivity" for cond in rule.conditions)
    assert any(cond.field == "principal.trust_level" for cond in rule.conditions)


def test_dsl_parse_supports_model_tag_condition():
    parsed, report = parse_source(
        "RULE: review_domestic_model\n"
        "PHASES: llm_before\n"
        'CONDITION: model.tag == "domestic"\n'
        "POLICY: HUMAN_CHECK\n"
    )

    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.condition_expr == 'model.tag == "domestic"'
    assert any(cond.field == "model.tag" for cond in rule.conditions)


def test_dsl_parse_supports_agent_tag_condition():
    parsed, report = parse_source(
        "RULE: review_local_agent\n"
        "PHASES: llm_before\n"
        'CONDITION: agent.tag == "local"\n'
        "POLICY: HUMAN_CHECK\n"
    )

    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.condition_expr == 'agent.tag == "local"'
    assert any(cond.field == "agent.tag" for cond in rule.conditions)


def test_policy_rule_to_source_preserves_agent_tag_name():
    rule = PolicyRule(
        rule_id="review_local_agent",
        effect=PolicyEffect.REQUIRE_APPROVAL,
        reason="review local agent",
        priority=70,
        event_types=["llm_input"],
        conditions=[RuleCondition(field="agent.tag", op="eq", value="local")],
        condition_expr='agent.tag == "local"',
        metadata={"dsl_conditions": [{"expr": 'agent.tag == "local"'}], "phases": ["llm_before"]},
    )

    source = policy_rule_to_source(rule)

    assert 'CONDITION: agent.tag == "local"' in source
    assert "model.tag" not in source


def test_policy_rule_to_source_preserves_model_tag_name():
    rule = PolicyRule(
        rule_id="review_domestic_model",
        effect=PolicyEffect.REQUIRE_APPROVAL,
        reason="review domestic model",
        priority=70,
        event_types=["llm_input"],
        conditions=[RuleCondition(field="model.tag", op="eq", value="domestic")],
        condition_expr='model.tag == "domestic"',
        metadata={"dsl_conditions": [{"expr": 'model.tag == "domestic"'}], "phases": ["llm_before"]},
    )

    source = policy_rule_to_source(rule)

    assert 'CONDITION: model.tag == "domestic"' in source
    assert "agent.tag" not in source


def test_dsl_parse_supports_unconditional_wildcard_condition():
    parsed, report = parse_source(
        "RULE: always_before_llm\n"
        "PHASES: llm_before\n"
        "CONDITION: *\n"
        "POLICY: ALLOW\n"
    )

    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.condition_expr == "*"
    assert rule.conditions == []


def test_published_llm_before_wildcard_deny_blocks_llm_input():
    con = _console()
    source = (
        "RULE: deny_all_before_llm\n"
        "PHASES: llm_before\n"
        "CONDITION: *\n"
        "POLICY: DENY\n"
        'Reason: "block all model input"'
    )

    publish = con.publish_rule("agent-alpha", source)
    assert publish["ok"] is True

    res = con.manager.decide(
        {
            "context": {"session_id": "s-llm", "agent_id": "agent-alpha"},
            "current_event": {
                "event_type": "llm_input",
                "payload": {"messages": [{"role": "user", "content": "hello"}]},
                "risk_signals": [],
            },
            "trajectory_window": [],
            "local_signals": [],
        }
    )

    assert res["decision"]["decision_type"] == "deny"
    assert res["plugin_result"]["metadata"]["rule_based_plugin"]["rule_id"] == "deny_all_before_llm"


def test_dsl_parse_and_roundtrip_preserves_llm_prompt():
    parsed, report = parse_source(_LLM_RULE)

    assert report.ok and len(parsed) == 1
    rule = parsed[0].rule
    assert rule.metadata["review_kind"] == "llm_check"
    assert rule.metadata["llm_prompt"] == "Decide allow or deny based on sensitivity."
    source = policy_rule_to_source(rule)
    assert 'Prompt: "Decide allow or deny based on sensitivity."' in source


def test_publish_list_delete_rule():
    con = _console()
    before = len(con.list_rules())
    res = con.publish_rule("agent-alpha", _DENY_RULE)
    assert res["ok"] is True and res["rule_id"] == "block_shell"

    rules = con.list_rules("agent-alpha")
    managed = [r for r in rules if r["user_managed"]]
    assert any(r["rule_id"] == "block_shell" for r in managed)
    # Published rule is available to the optional rule-based checker.
    published = next(r for r in con.manager.policy.store.rules() if r.rule_id == "block_shell")
    assert published.agent_id == "agent-alpha"
    assert published.metadata["agent_scope"] == "agent-alpha"
    assert published.metadata["scope_injected"] is False

    dup = con.publish_rule("agent-alpha", _DENY_RULE)
    assert dup["ok"] is False  # duplicate id

    deleted = con.delete_rule("agent-alpha", "block_shell")
    assert deleted["ok"] is True
    assert len(con.list_rules()) == before


def test_published_rule_only_matches_owning_agent():
    con = _console()
    res = con.publish_rule("agent-alpha", _NO_CONDITION_RULE)
    assert res["ok"] is True

    published = next(r for r in con.manager.policy.store.rules() if r.rule_id == "allow_safe_read")
    alpha_event = tool_event(
        RuntimeContext(session_id="s-alpha", agent_id="agent-alpha", user_id="u1"),
        "read_file",
        {},
    )
    beta_event = tool_event(
        RuntimeContext(session_id="s-beta", agent_id="agent-beta", user_id="u1"),
        "read_file",
        {},
    )

    assert published.matches(alpha_event) is True
    assert published.matches(beta_event) is False
    assert [rule.rule_id for rule in con.manager.policy.store.rules_for_agent("agent-alpha") if rule.rule_id == "allow_safe_read"] == ["allow_safe_read"]
    assert [rule.rule_id for rule in con.manager.policy.store.rules_for_agent("agent-beta") if rule.rule_id == "allow_safe_read"] == []


def test_observer_records_traffic_audit_and_tickets():
    con = _console()
    _seed_runtime_rules(con)
    mgr = con.manager
    # deny via exfiltration
    mgr.decide({
        "context": {"session_id": "s1", "agent_id": "agent-alpha"},
        "current_event": {
            "event_type": "tool_invoke",
            "payload": {"tool_name": "send_email", "capabilities": ["external_send"]},
        },
        "trajectory_window": [{
            "event_type": "tool_result",
            "payload": {"tool_name": "file.read", "result": "sk-ABCD1234secret"},
            "risk_signals": ["secret_detected"],
        }],
    })
    # held decision -> approval ticket
    mgr.decide({
        "context": {"session_id": "s2", "agent_id": "agent-alpha"},
        "current_event": {
            "event_type": "tool_invoke",
            "payload": {"tool_name": "payments.charge", "capabilities": ["payment"]},
        },
        "trajectory_window": [],
    })

    traffic = con.traffic("agent-alpha")
    assert len(traffic) == 2
    assert any(e["action"] == "deny" for e in traffic)
    assert len(con.audit_recent("agent-alpha")) == 2

    tickets = con.approvals("agent-alpha")
    assert len(tickets) == 1
    tid = tickets[0]["ticket_id"]
    assert con.resolve_ticket(tid, approved=True) is True
    assert con.approvals("agent-alpha") == []


def test_observer_exposes_plugin_summary_metadata():
    class FakeDiagnosticPlugin(BasePlugin):
        name = "diagnostic"
        event_types = [EventType.TOOL_INVOKE]

        def check(self, event, context, trajectory_window=None):
            return CheckResult(
                metadata={
                    "diagnostic": {
                        "prediction": 0,
                        "label": "safe",
                        "reason": "allowed by fake diagnostic plugin",
                    }
                }
            )

    con = ConsoleState(
        RuntimeManager(
            plugin_config={
                "phases": {
                    "tool_before": {"client": [], "server": [FakeDiagnosticPlugin]}
                }
            }
        )
    )

    con.manager.decide(
        {
            "context": {"session_id": "s-diagnostic", "agent_id": "agent-alpha"},
            "current_event": {
                "event_type": "tool_invoke",
                "payload": {"tool_name": "send_email", "arguments": {}, "capabilities": []},
            },
            "trajectory_window": [],
        }
    )

    traffic = con.traffic("agent-alpha")
    assert traffic[0]["plugin_summary"][0]["name"] == "diagnostic"
    assert traffic[0]["plugin_summary"][0]["label"] == "safe"

    audit = con.audit_recent("agent-alpha")
    decision = audit[0]["decision"]
    assert decision["plugin_result"]["metadata"]["diagnostic"]["prediction"] == 0
    assert decision["plugin_summary"][0]["reason"] == "allowed by fake diagnostic plugin"


def test_audit_recent_exposes_runtime_payload_and_mcp_metadata():
    con = _console()
    con.manager.decide(
        {
            "context": {"session_id": "s-mcp-runtime", "agent_id": "agent-alpha"},
            "current_event": {
                "event_type": "tool_result",
                "payload": {
                    "tool_name": "local_mcp__read_file",
                    "result": "{\"content\":[{\"type\":\"text\",\"text\":\"hello\"}]}",
                },
                "metadata": {
                    "toolSource": "mcp",
                    "sourceFramework": "mcp_native",
                    "mcp_unique_id": "agent-alpha:mcp-sha",
                    "mcp_name": "local_mcp",
                    "mcp_tool_name": "read_file",
                    "mcp_transport": "stdio",
                    "mcp_remote": False,
                },
            },
            "trajectory_window": [],
        }
    )

    audit = con.audit_recent("agent-alpha")[0]
    event = audit["event"]
    runtime_state = audit["runtime_state"]

    assert event["tool_call"]["result"] == "{\"content\":[{\"type\":\"text\",\"text\":\"hello\"}]}"
    assert event["tool_call"]["source"] == "mcp"
    assert event["tool_call"]["mcp"]["mcp_name"] == "local_mcp"
    assert event["tool_call"]["mcp"]["mcp_tool_name"] == "read_file"
    assert runtime_state["result"] == "{\"content\":[{\"type\":\"text\",\"text\":\"hello\"}]}"
    assert runtime_state["metadata"]["toolSource"] == "mcp"
    assert runtime_state["mcp"]["mcp_name"] == "local_mcp"
    assert runtime_state["mcp"]["mcp_tool_name"] == "read_file"


def test_audit_recent_exposes_model_metadata_from_context():
    con = _console()
    event = llm_input(
        RuntimeContext(
            session_id="s-llm-model",
            agent_id="agent-alpha",
            metadata={
                "model": {
                    "provider": "openai",
                    "name": "gpt-5.2",
                    "base_url": "https://api.gpt.ge/v1",
                    "source": "openclaw-runtime",
                }
            },
        ),
        [{"role": "user", "content": "hello"}],
        phase="llm_before",
    )

    con._observe(  # noqa: SLF001
        event,
        GuardDecision.allow("ok"),
        {"plugin_result": {"is_final": False, "metadata": {}}},
    )

    runtime_state = con.audit_recent("agent-alpha")[0]["runtime_state"]
    assert runtime_state["model"] == {
        "provider": "openai",
        "name": "gpt-5.2",
        "base_url": "https://api.gpt.ge/v1",
        "source": "openclaw-runtime",
    }
    assert runtime_state["metadata"]["model"] == runtime_state["model"]


def test_health_reports_rule_counts():
    con = _console()
    _seed_runtime_rules(con)
    health = con.health()
    assert health["ok"] is True
    assert health["rules"] >= 1
    assert "rule_version" in health


def test_register_tool_adds_or_updates_console_catalog():
    con = _console()
    tool = con.register_tool(
        {"agent_id": "live-agent"},
        {
            "name": "docs.search",
            "input_params": ["query"],
            "labels": {
                "boundary": "internal",
                "sensitivity": "moderate",
                "integrity": "trusted",
                "tags": ["read_only"],
            },
        },
    )
    assert tool is not None
    assert tool["owner_agent_id"] == "live-agent"
    assert tool["name"] == "docs.search"
    assert tool["input_params"] == ["query"]

    scoped = con.tools("live-agent")
    assert any(item["name"] == "docs.search" for item in scoped)


def test_sync_tools_replaces_console_catalog_for_agent():
    con = _console()
    con.register_tool({"agent_id": "live-agent"}, {"name": "old.tool"})

    result = con.sync_tools(
        {"agent_id": "live-agent"},
        [
            {
                "name": "docs.search",
                "input_params": ["query"],
                "labels": {"tags": ["read_only"]},
            }
        ],
    )

    assert result is not None
    assert result["tool_count"] == 1
    scoped = con.tools("live-agent")
    assert [item["name"] for item in scoped] == ["docs.search"]
    assert scoped[0]["input_params"] == ["query"]
    assert scoped[0]["labels"]["tags"] == ["read_only"]


def test_sync_tools_preserves_explicit_empty_required_args():
    con = _console()

    result = con.sync_tools(
        {"agent_id": "live-agent"},
        [
            {
                "name": "queryEnterpriseInfo",
                "input_params": ["company_name", "credit_code"],
                "required_args": [],
                "schema": {
                    "type": "object",
                    "properties": {
                        "company_name": {},
                        "credit_code": {},
                    },
                    "required": [],
                },
            }
        ],
    )

    assert result is not None
    scoped = con.tools("live-agent")
    assert scoped[0]["input_params"] == ["company_name", "credit_code"]
    assert scoped[0]["required_args"] == []
    assert scoped[0]["schema"]["required"] == []


def test_sync_tools_scopes_dify_catalog_by_external_email():
    con = _console()
    con.sync_tools(
        {
            "agent_id": "dify-workflow:alice-app",
            "metadata": {
                "adapter": "dify",
                "dify_user_email": "Alice@Example.COM",
            },
        },
        [{"name": "docs.search"}],
    )
    con.sync_tools(
        {
            "agent_id": "dify-workflow:bob-app",
            "metadata": {
                "adapter": "dify",
                "external_account_email": "bob@example.com",
            },
        },
        [{"name": "mail.send"}],
    )

    alice_tools = con.tools(visible_to_external_accounts={("dify", "alice@example.com")})
    bob_tools = con.tools(visible_to_external_accounts={("dify", "bob@example.com")})
    unbound_tools = con.tools(visible_to_external_accounts=set())

    assert [item["owner_agent_id"] for item in alice_tools] == ["dify-workflow:alice-app"]
    assert [item["owner_agent_id"] for item in bob_tools] == ["dify-workflow:bob-app"]
    assert unbound_tools == []


def test_register_skills_stores_skill_record_resource_and_detection_state():
    con = _console()
    result = con.register_skills(
        {
            "agent_id": "skill-agent",
            "user_id": "skill-user",
            "session_id": "skill-session",
        },
        [
            {
                "name": "demo-skill",
                "description": "Demo skill",
                "source_framework": "openclaw_compatible",
                "object_type": "skill",
                "root_path": "/tmp/demo",
                "entry_file": "SKILL.md",
                "sha256": "a" * 64,
                "file_count": 2,
                "total_size": 1234,
                "extraction": {"level": "directory", "confidence": "high"},
                "skill_markdown": {"relative_path": "SKILL.md", "content": "# Demo"},
                "files": [{"relative_path": "SKILL.md", "content": "# Demo"}],
            }
        ],
    )

    assert result is not None
    assert result["skill_count"] == 1

    scoped = con.skills("skill-agent")
    assert len(scoped) == 1
    skill = scoped[0]
    assert skill["owner_agent_id"] == "skill-agent"
    assert skill["agent_id"] == "skill-agent"
    assert skill["user_id"] == "skill-user"
    assert skill["session_id"] == "skill-session"
    assert skill["skill_unique_id"] == f"skill-agent:{'a' * 64}"
    assert skill["detect_result"] is None
    assert skill["skill_resource"]["skill_markdown"]["content"] == "# Demo"
    assert skill["descriptor"]["files"][0]["relative_path"] == "SKILL.md"
    assert "skill-agent" in con.agents()


def test_register_skills_sync_inventory_removes_stale_records_and_preserves_detection():
    con = _console()
    first = con.register_skills(
        {"agent_id": "skill-agent", "user_id": "skill-user", "session_id": "session-a"},
        [
            {
                "name": "keep-skill",
                "sha256": "a" * 64,
                "detect_result": {
                    "risk_level": "high",
                    "label": "malicious",
                    "reason": "previous scan result",
                },
            },
            {
                "name": "stale-skill",
                "sha256": "b" * 64,
            },
        ],
    )
    assert first is not None
    assert first["skill_count"] == 2

    second = con.register_skills(
        {"agent_id": "skill-agent", "user_id": "skill-user", "session_id": "session-b"},
        [
            {
                "name": "keep-skill",
                "sha256": "a" * 64,
            }
        ],
        scan={"sync_inventory": True},
    )

    assert second is not None
    scoped = con.skills("skill-agent")
    assert [item["name"] for item in scoped] == ["keep-skill"]
    assert scoped[0]["session_id"] == "session-b"
    assert scoped[0]["detect_result"]["risk_level"] == "high"
    assert scoped[0]["detect_result"]["label"] == "malicious"


def test_register_mcps_sync_inventory_removes_stale_records_and_preserves_detection():
    con = _console()
    first = con.register_mcps(
        {"agent_id": "mcp-agent", "user_id": "mcp-user", "session_id": "session-a"},
        [
            {
                "name": "keep-mcp",
                "sha256": "a" * 64,
                "detect_result": {
                    "risk_level": "medium",
                    "label": "suspicious",
                    "reason": "previous scan result",
                },
            },
            {
                "name": "stale-mcp",
                "sha256": "b" * 64,
            },
        ],
    )
    assert first is not None
    assert first["mcp_count"] == 2

    second = con.register_mcps(
        {"agent_id": "mcp-agent", "user_id": "mcp-user", "session_id": "session-b"},
        [
            {
                "name": "keep-mcp",
                "sha256": "a" * 64,
            }
        ],
        scan={"sync_inventory": True},
    )

    assert second is not None
    scoped = con.mcps("mcp-agent")
    assert [item["name"] for item in scoped] == ["keep-mcp"]
    assert scoped[0]["session_id"] == "session-b"
    assert scoped[0]["detect_result"]["risk_level"] == "medium"
    assert scoped[0]["detect_result"]["label"] == "suspicious"


def test_generate_rule_uses_agent_context_and_returns_candidate(monkeypatch):
    con = _console()
    con.register_tool(
        {"agent_id": "agent-alpha"},
        {
            "name": "email.send",
            "input_params": ["to", "body"],
            "labels": {"boundary": "external", "sensitivity": "moderate", "integrity": "trusted"},
        },
    )

    observed: dict[str, object] = {}

    class _FakeWorkflow:
        def __init__(self, **kwargs):
            observed["init"] = kwargs

        def generate(self, request):
            observed["request"] = request
            validation = type(
                "_Validation",
                (),
                {
                    "to_dict": lambda self: {
                        "ok": True,
                        "errors": [],
                        "warnings": [],
                        "parsed_dsl_rules": [],
                        "normalized_rules": [],
                    }
                },
            )()
            candidate = type(
                "_Candidate",
                (),
                {"payload": {"summary": "generated", "rules": []}, "validation": validation},
            )()
            return type(
                "_Session",
                (),
                {
                    "request": request,
                    "stop_reason": "ready_for_user_review",
                    "attempts": [],
                    "accepted_candidate": candidate,
                    "latest_candidate": candidate,
                    "user_feedback_history": [],
                    "remaining_rounds": 3,
                },
            )()

    monkeypatch.setattr("backend.console.state.LLMRuleGeneratorWorkflow", _FakeWorkflow)

    result = con.generate_rule("agent-alpha", "限制对外发邮件")

    assert result["ok"] is True
    assert result["agent_id"] == "agent-alpha"
    request = observed["request"]
    assert request.agent_id == "agent-alpha"
    assert request.user_requirement == "限制对外发邮件"
    assert any(tool["name"] == "email.send" for tool in request.tool_catalog)
    assert isinstance(request.existing_rules, list)


def test_generate_rule_refine_requires_valid_current_candidate(monkeypatch):
    con = _console()

    class _FakeWorkflow:
        def __init__(self, **kwargs):
            pass

        def validate_candidate(self, payload, request):
            class _Validation:
                ok = False

                def to_dict(self):
                    return {"ok": False, "errors": [{"code": "bad", "message": "bad candidate"}], "warnings": [], "parsed_dsl_rules": [], "normalized_rules": []}

            return _Validation()

    monkeypatch.setattr("backend.console.state.LLMRuleGeneratorWorkflow", _FakeWorkflow)

    result = con.generate_rule(
        "agent-alpha",
        "限制对外发邮件",
        user_feedback="改成 review",
        current_candidate={"summary": "old", "rules": []},
    )

    assert result["ok"] is False
    assert result["error"] == "current_candidate failed validation"


class _ConsoleHumanCheckPlugin(BasePlugin):
    name = "console_human_check"
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event, context, trajectory_window=None):
        return CheckResult(
            decision_candidate=GuardDecision.human_check(
                "console review",
                policy_id="server:console-review",
            ),
            risk_signals=["console_human_check_seen"],
            is_final=True,
        )


class _ConsoleSecondPlugin(BasePlugin):
    name = "console_second"
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event, context, trajectory_window=None):
        return CheckResult(risk_signals=["console_second_seen"])


class _ConsoleHumanCheckWithContentPlugin(BasePlugin):
    name = "console_human_check_with_content"
    event_types = [EventType.TOOL_INVOKE]

    def check(self, event, context, trajectory_window=None):
        return CheckResult(
            decision_candidate=GuardDecision.human_check(
                "console rewrite review",
                processed_content='{"input": "console rewrite"}',
                policy_id="server:console-rewrite-review",
            ),
            risk_signals=["console_human_check_with_content_seen"],
            is_final=True,
        )


def test_audit_recent_keeps_plugin_outcomes():
    con = ConsoleState(
        RuntimeManager(
            plugin_config={
                "phases": {
                    "tool_before": {
                        "client": [],
                        "server": [_ConsoleHumanCheckPlugin, _ConsoleSecondPlugin],
                    }
                }
            },
            enable_session_health_monitor=False,
        )
    )
    con.manager.decide(
        {
            "context": {"session_id": "s-audit", "agent_id": "agent-audit"},
            "current_event": {
                "event_type": "tool_invoke",
                "payload": {"tool_name": "read_file", "arguments": {}, "capabilities": []},
            },
            "trajectory_window": [],
            "local_signals": [],
        }
    )

    audit = con.audit_recent("agent-audit")

    assert len(audit) == 1
    outcomes = audit[0]["decision"].get("plugin_outcomes") or []
    assert [item["plugin"] for item in outcomes] == ["console_human_check", "console_second", "rule_based_plugin"]


def test_console_exposes_processed_content_for_review_decisions():
    con = ConsoleState(
        RuntimeManager(
            plugin_config={
                "phases": {
                    "tool_before": {
                        "client": [],
                        "server": [_ConsoleHumanCheckWithContentPlugin],
                    }
                }
            },
            enable_session_health_monitor=False,
        )
    )
    con.manager.decide(
        {
            "context": {"session_id": "s-approval", "agent_id": "agent-approval"},
            "current_event": {
                "event_type": "tool_invoke",
                "payload": {"tool_name": "read_file", "arguments": {}, "capabilities": []},
            },
            "trajectory_window": [],
            "local_signals": [],
        }
    )

    audit = con.audit_recent("agent-approval")[0]["decision"]
    assert audit["decision_type"] == "human_check"
    assert audit["processed_content"] == '{"input": "console rewrite"}'

    approval = con.approvals("agent-approval")[0]["decision"]
    assert approval["decision_type"] == "human_check"
    assert approval["processed_content"] == '{"input": "console rewrite"}'
