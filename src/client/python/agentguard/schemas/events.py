"""RuntimeEvent: normalized representation of any runtime behavior."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentguard.schemas.context import RuntimeContext
from agentguard.utils.hash import stable_hash
from agentguard.utils.time import now_ts


class EventType(str, Enum):
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    TOOL_INVOKE = "tool_invoke"
    TOOL_RESULT = "tool_result"

    # Deprecated event types intentionally kept out of the active enum:
    # user_input, llm_thought, llm_tool_call_candidate, memory_read,
    # memory_write, file_read, file_write, network_request, final_response,
    # sandbox_execution, policy_decision.


# Patterns used for redaction of sensitive payload values.
_SECRET_KEY_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "access_key",
    "private_key",
)
_REDACT_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{12,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\b\d{13,19}\b"),  # card-like
]
_REDACTED = "[REDACTED]"


def _redact_value(value: Any, key: str | None = None) -> Any:
    if key and any(h in key.lower() for h in _SECRET_KEY_HINTS):
        return _REDACTED
    if isinstance(value, str):
        out = value
        for pat in _REDACT_PATTERNS:
            out = pat.sub(_REDACTED, out)
        return out
    if isinstance(value, dict):
        return {k: _redact_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    return value


@dataclass
class RuntimeEvent:
    """A single normalized runtime event."""

    event_id: str
    event_type: EventType
    timestamp: float
    context: RuntimeContext
    payload: dict[str, Any]
    risk_signals: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "context": self.context.to_dict(),
            "payload": self.payload,
            "risk_signals": list(self.risk_signals),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeEvent":
        return cls(
            event_id=data.get("event_id") or _new_id(),
            event_type=EventType(data["event_type"]),
            timestamp=float(data.get("timestamp") or now_ts()),
            context=RuntimeContext.from_dict(data.get("context") or {}),
            payload=dict(data.get("payload") or {}),
            risk_signals=list(data.get("risk_signals") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    def redacted(self) -> "RuntimeEvent":
        """Return a copy with secrets removed from payload/metadata."""
        return RuntimeEvent(
            event_id=self.event_id,
            event_type=self.event_type,
            timestamp=self.timestamp,
            context=self.context,
            payload=_redact_value(self.payload),
            risk_signals=list(self.risk_signals),
            metadata=_redact_value(self.metadata),
        )

    def stable_hash(self) -> str:
        """Deterministic hash ignoring volatile fields (id/timestamp)."""
        return stable_hash(
            {
                "event_type": self.event_type.value,
                "context": {
                    "session_id": self.context.session_id,
                    "policy": self.context.policy,
                    "policy_version": self.context.policy_version,
                },
                "payload": self.payload,
                "risk_signals": sorted(self.risk_signals),
            }
        )

    def add_signal(self, signal: str) -> None:
        if signal and signal not in self.risk_signals:
            self.risk_signals.append(signal)


def _new_id() -> str:
    return f"evt_{uuid.uuid4().hex[:16]}"


def _make(
    event_type: EventType,
    context: RuntimeContext,
    payload: dict[str, Any] | None = None,
    *,
    risk_signals: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=_new_id(),
        event_type=event_type,
        timestamp=now_ts(),
        context=context,
        payload=payload or {},
        risk_signals=risk_signals or [],
        metadata=metadata or {},
    )


# ---- helper constructors ----------------------------------------------
def user_input(context: RuntimeContext, text: str, **meta: Any) -> RuntimeEvent:
    """Compatibility alias: user text is now represented as LLM_INPUT."""
    return _make(
        EventType.LLM_INPUT,
        context,
        {"text": text, "messages": [{"role": "user", "content": text}]},
        metadata=meta,
    )


def llm_input(context: RuntimeContext, messages: Any, **meta: Any) -> RuntimeEvent:
    return _make(EventType.LLM_INPUT, context, {"messages": messages}, metadata=meta)


def llm_output(context: RuntimeContext, output: Any, **meta: Any) -> RuntimeEvent:
    return _make(EventType.LLM_OUTPUT, context, {"output": output}, metadata=meta)


def llm_thought(context: RuntimeContext, thought: str, **meta: Any) -> RuntimeEvent:
    """Compatibility alias: thoughts are no longer a separate event type."""
    return _make(EventType.LLM_OUTPUT, context, {"output": thought}, metadata=meta)


def tool_invoke(
    context: RuntimeContext,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    capabilities: list[str] | None = None,
    **meta: Any,
) -> RuntimeEvent:
    payload = {
        "tool_name": tool_name,
        "arguments": arguments,
        "capabilities": capabilities or [],
    }
    return _make(EventType.TOOL_INVOKE, context, payload, metadata=meta)


def tool_result(
    context: RuntimeContext,
    tool_name: str,
    result: Any,
    *,
    error: str | None = None,
    **meta: Any,
) -> RuntimeEvent:
    payload = {"tool_name": tool_name, "result": result, "error": error}
    return _make(EventType.TOOL_RESULT, context, payload, metadata=meta)


def final_response(context: RuntimeContext, text: str, **meta: Any) -> RuntimeEvent:
    """Compatibility alias: final text is now represented as LLM_OUTPUT."""
    return _make(EventType.LLM_OUTPUT, context, {"output": text}, metadata=meta)
