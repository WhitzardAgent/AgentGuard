"""Explainability: human-readable summaries of decisions."""

from __future__ import annotations

from agentguard.models.decisions import Decision
from agentguard.models.events import RuntimeEvent


def explain_decision(event: RuntimeEvent, decision: Decision) -> str:
    """Produce a human-readable explanation of why a decision was made."""
    tool = event.tool_call.tool_name if event.tool_call else "unknown"
    agent = event.principal.agent_id
    user_id = event.principal.user_id
    role = event.principal.role
    trust = event.principal.trust_level

    lines = [
        f"Tool: {tool}",
        f"Agent: {agent} (role={role}, trust={trust})",
        f"User ID: {user_id}" if user_id is not None else "User ID: <none>",
        f"Decision: {decision.action.value}",
        f"Risk Score: {decision.risk_score}",
    ]
    if decision.matched_rules:
        lines.append(f"Matched Rules: {', '.join(decision.matched_rules)}")
    if decision.reason:
        lines.append(f"Reason: {decision.reason}")
    if decision.degrade_profile:
        lines.append(f"Degrade Profile: {decision.degrade_profile}")
    if decision.obligations:
        obs = [f"{o.kind}({o.params})" for o in decision.obligations]
        lines.append(f"Obligations: {'; '.join(obs)}")
    return "\n".join(lines)
