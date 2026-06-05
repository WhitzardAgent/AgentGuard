"""Translation between the Harness (v2) schemas and the server (v1) schemas.

The remote AgentGuard PDP (``POST /v1/evaluate``) speaks the server-side
``agentguard.models`` schema (``RuntimeEvent`` with ``Principal``/``ToolCall``
and the 4-value ``Action`` enum). The client-side Harness uses the lighter
``agentguard.schemas`` models with the 7-value ``DecisionAction`` enum.

This module bridges the two so the dual-path enforcer can escalate to a real,
unmodified server without leaking schema details into the rest of the Harness.
"""

from __future__ import annotations

from typing import Any

from agentguard.models.decisions import Action as ServerAction
from agentguard.models.decisions import ClientAction as ServerClientAction
from agentguard.models.decisions import Decision as ServerDecision
from agentguard.models.events import EventType as ServerEventType
from agentguard.models.events import (
    Principal,
    RuntimeEvent as ServerRuntimeEvent,
    ToolCall,
    ToolStaticLabel,
)
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import Decision, DecisionAction, Obligation
from agentguard.schemas.events import EventType, RuntimeEvent

# Map client capability list → server tool boundary (coarse but useful).
_PRIVILEGED_CAPS = {"shell", "exec", "delete"}
_EXTERNAL_CAPS = {"network"}

# Harness event types that make sense to escalate to the server PDP.
TOOLISH_EVENTS = {EventType.TOOL_CALL, EventType.NETWORK_ACTION, EventType.FILE_OP}

_SINK_BY_EVENT = {
    EventType.NETWORK_ACTION: "http",
    EventType.FILE_OP: "fs_write",
}


def _boundary_for(capabilities: list[str]) -> str:
    caps = set(capabilities)
    if caps & _PRIVILEGED_CAPS:
        return "privileged"
    if caps & _EXTERNAL_CAPS:
        return "external"
    return "internal"


def to_server_event(event: RuntimeEvent, context: RuntimeContext) -> ServerRuntimeEvent:
    """Convert a Harness event into a server-side ``RuntimeEvent``."""
    principal = Principal(
        agent_id=context.agent_id or event.agent_id or "harness-client",
        session_id=context.session_id,
        user_id=context.user_id,
    )
    sink = event.sink_type if event.sink_type != "none" else _SINK_BY_EVENT.get(event.type, "none")
    tool_call = ToolCall(
        tool_name=event.tool_name or event.type.value,
        args=dict(event.args),
        target=_extract_target(event),
        sink_type=sink,  # type: ignore[arg-type]
        label=ToolStaticLabel(boundary=_boundary_for(event.capabilities)),  # type: ignore[arg-type]
        syntax=list(event.args.keys()),
    )
    return ServerRuntimeEvent(
        event_type=ServerEventType.TOOL_CALL_ATTEMPT,
        principal=principal,
        tool_call=tool_call,
        goal=context.goal,
        scope=list(context.scope),
        extra={"harness_event_type": event.type.value, **dict(event.annotations)},
    )


def _extract_target(event: RuntimeEvent) -> dict[str, Any]:
    target: dict[str, Any] = {}
    args = event.args
    if "url" in args:
        import urllib.parse

        try:
            parsed = urllib.parse.urlparse(str(args["url"]))
            target["url"] = args["url"]
            target["domain"] = parsed.hostname or ""
        except Exception:
            target["url"] = args["url"]
    if "path" in args:
        target["path"] = args["path"]
    if "to" in args and isinstance(args["to"], str) and "@" in args["to"]:
        target["domain"] = args["to"].split("@", 1)[1]
    return target


# Server Action / ClientAction → Harness DecisionAction
_ACTION_MAP: dict[ServerAction, DecisionAction] = {
    ServerAction.ALLOW: DecisionAction.ALLOW,
    ServerAction.DENY: DecisionAction.DENY,
    ServerAction.DEGRADE: DecisionAction.DEGRADE,
    ServerAction.HUMAN_CHECK: DecisionAction.REQUIRE_APPROVAL,
    ServerAction.LLM_CHECK: DecisionAction.REQUIRE_APPROVAL,
}
_CLIENT_ACTION_MAP: dict[ServerClientAction, DecisionAction] = {
    ServerClientAction.ALLOW: DecisionAction.ALLOW,
    ServerClientAction.DENY: DecisionAction.DENY,
    ServerClientAction.HUMAN_CHECK: DecisionAction.REQUIRE_APPROVAL,
}


def from_server_decision(payload: dict[str, Any]) -> Decision:
    """Convert a ``/v1/evaluate`` response body into a Harness :class:`Decision`.

    Accepts the full response envelope ``{"decision": {...}, "client_action": ...}``
    or a bare decision dict.
    """
    decision_data = payload.get("decision", payload) if isinstance(payload, dict) else {}
    client_action_str = payload.get("client_action") if isinstance(payload, dict) else None

    server = ServerDecision.model_validate(decision_data)

    action = _ACTION_MAP.get(server.action, DecisionAction.ALLOW)
    # A resolved client_action is authoritative when present.
    if client_action_str:
        try:
            action = _CLIENT_ACTION_MAP.get(ServerClientAction(client_action_str), action)
        except ValueError:
            pass

    obligations = [
        Obligation(kind=o.kind, params=dict(o.params)) for o in server.obligations
    ]
    return Decision(
        action=action,
        reason=server.reason or f"pdp:{server.action.value}",
        risk_score=server.risk_score,
        matched_rules=list(server.matched_rules),
        obligations=obligations,
        source="pdp",
        metadata={"server_action": server.action.value, "rule_version": server.rule_version},
    )
