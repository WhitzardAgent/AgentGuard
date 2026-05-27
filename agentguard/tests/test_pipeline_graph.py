"""Round 2 — chain-rule / graph-feature integration tests.

Verifies that ``exists_path(source_label IN {...})`` predicates inside
the policy fire when (and only when) downstream tool calls carry
``ProvenanceRef`` entries that match the configured source labels.

These tests use the high-level ``Guard`` facade (in monitor mode so
decisions are returned to the caller without enforcement side-effects)
plus a single chain rule. They check the *fast-path* feature
collection does the right thing in three regimes:

  1. provenance present + args unknown   → DENY (chain rule fires)
  2. provenance present + args trusted   → ALLOW (whitelist beats chain)
  3. no provenance                       → ALLOW (chain doesn't fire)
"""

from __future__ import annotations

import pytest

from agentguard import Guard
from agentguard.models.decisions import Action
from agentguard.graph.model import NodeType
from agentguard.models.events import (
    EventType,
    Principal,
    ProvenanceRef,
    RuntimeEvent,
    ToolCall,
)


# A single chain rule + the matching ALLOW so we can demonstrate priority.
CHAIN_POLICY = '''
RULE: allow_known_iban
ON: tool_call(send_money)
CONDITION: args.recipient IN whitelist("user_known_ibans")
POLICY: ALLOW

RULE: deny_chain_send_money
ON: tool_call(send_money)
CONDITION: exists_path(source_label IN {"untrusted.user_content"}, max_hops=6)
  AND args.recipient NOT IN whitelist("user_known_ibans")
POLICY: DENY
'''


@pytest.fixture
def guard():
    g = Guard(policy_source=CHAIN_POLICY, builtin_rules=False, mode="monitor")
    yield g
    g.close()


def _principal(session_id: str = "sess-chain") -> Principal:
    return Principal(
        agent_id="agent-1",
        session_id=session_id,
        role="basic",
        trust_level=1,
    )


def _event(
    *,
    session_id: str,
    recipient: str,
    refs: list[ProvenanceRef] | None = None,
    allowlists: dict[str, list[str]] | None = None,
) -> RuntimeEvent:
    extra: dict = {}
    if allowlists:
        extra["allowlists"] = allowlists
    return RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=_principal(session_id),
        tool_call=ToolCall(
            tool_name="send_money",
            args={"recipient": recipient, "amount": 100},
        ),
        provenance_refs=refs or [],
        extra=extra,
    )


# --------------------------------------------------------------------- #
# Scenario 1 — chain rule fires
# --------------------------------------------------------------------- #
def test_chain_rule_fires_on_external_provenance(guard: Guard):
    refs = [
        ProvenanceRef(
            node_id="upstream-1",
            label="untrusted.user_content",
            parent_tool_call_id="upstream-1",
        )
    ]
    decision = guard.pipeline.handle_attempt(
        _event(
            session_id="sess-1",
            recipient="GB99ATTACKER",
            refs=refs,
            allowlists={"user_known_ibans": ["GB12TRUSTED"]},
        )
    )
    assert decision.action is Action.DENY
    assert "deny_chain_send_money" in decision.matched_rules


# --------------------------------------------------------------------- #
# Scenario 2 — whitelist wins over chain rule
# --------------------------------------------------------------------- #
def test_whitelist_overrides_chain(guard: Guard):
    refs = [
        ProvenanceRef(
            node_id="upstream-2",
            label="untrusted.user_content",
            parent_tool_call_id="upstream-2",
        )
    ]
    decision = guard.pipeline.handle_attempt(
        _event(
            session_id="sess-2",
            recipient="GB12TRUSTED",
            refs=refs,
            allowlists={"user_known_ibans": ["GB12TRUSTED"]},
        )
    )
    assert decision.action is Action.ALLOW
    assert "allow_known_iban" in decision.matched_rules
    assert "deny_chain_send_money" not in decision.matched_rules


# --------------------------------------------------------------------- #
# Scenario 3 — no upstream → chain does NOT fire
# --------------------------------------------------------------------- #
def test_no_provenance_no_chain(guard: Guard):
    decision = guard.pipeline.handle_attempt(
        _event(
            session_id="sess-3",
            recipient="GB99NEW",
            refs=None,
            allowlists={"user_known_ibans": ["GB12TRUSTED"]},
        )
    )
    # No chain rule fires; allow_known_iban doesn't match either; no match
    # → default ALLOW (the FastEvaluator's "no rule matched" case).
    assert decision.action is Action.ALLOW
    assert "deny_chain_send_money" not in (decision.matched_rules or [])


# --------------------------------------------------------------------- #
# Scenario 4 — multi-hop: history accumulates across calls in the same
# session.  A first read_file event populates the cache labels, and a
# subsequent send_money event in the same session inherits them via
# its own provenance_refs (mirrors what AgentGuardInterceptor does).
# --------------------------------------------------------------------- #
def test_chain_rule_fires_after_multi_hop_session(guard: Guard):
    sess = "sess-multi"
    # 1) "read_file" emitted by the agent (no rule matches → ALLOW).
    read_event = RuntimeEvent(
        event_type=EventType.TOOL_CALL_ATTEMPT,
        principal=_principal(sess),
        tool_call=ToolCall(tool_name="read_file", args={"path": "bill.txt"}),
        provenance_refs=[
            ProvenanceRef(
                node_id="read-1",
                label="untrusted.user_content",
                parent_tool_call_id="read-1",
            )
        ],
    )
    guard.pipeline.handle_attempt(read_event)

    # 2) The interceptor now wires the next call's provenance to the
    #    upstream read_file event_id (simulated here directly).
    follow_up = _event(
        session_id=sess,
        recipient="GB99ATTACKER",
        refs=[
            ProvenanceRef(
                node_id=f"{read_event.event_id}:untrusted.user_content",
                label="untrusted.user_content",
                parent_tool_call_id=read_event.event_id,
            )
        ],
        allowlists={"user_known_ibans": ["GB12TRUSTED"]},
    )
    decision = guard.pipeline.handle_attempt(follow_up)
    assert decision.action is Action.DENY
    assert "deny_chain_send_money" in decision.matched_rules


# --------------------------------------------------------------------- #
# Scenario 5 — `path_specs` are surfaced on CompiledRule for use by
# Pipeline._fast_features. This is a unit-style guard against
# accidental regressions in the compiler.
# --------------------------------------------------------------------- #
def test_compiled_rule_exposes_path_specs(guard: Guard):
    rule = next(
        r
        for r in guard.active_rules()
        if r.rule_id == "deny_chain_send_money"
    )
    assert rule.path_specs, "chain rule should expose path_specs"
    spec = rule.path_specs[0]
    assert spec.source_labels == ("untrusted.user_content",)
    assert spec.max_hops == 6
    assert spec.feature_key.startswith("graph.exists_path.")


def test_graph_writer_persists_principal_user_id(guard: Guard):
    ev = _event(
        session_id="sess-user-graph",
        recipient="GB00USER",
        allowlists={"user_known_ibans": ["GB12TRUSTED"]},
    )
    ev.principal.user_id = "user-graph"

    guard.pipeline.handle_attempt(ev)
    guard._graph_writer.flush()

    node = guard._graph_store._nodes[(NodeType.AGENT, ev.principal.agent_id)]
    assert node["user_id"] == "user-graph"
