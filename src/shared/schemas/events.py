"""RuntimeEvent: normalized representation of any runtime behavior."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeAlias

from shared.schemas.context import RuntimeContext
from shared.utils.hash import stable_hash
from shared.utils.time import now_ts


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


class _PayloadMapping:
    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]


@dataclass
class LLMInput(_PayloadMapping):
    messages: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"messages": [dict(item) for item in self.messages]}


@dataclass
class LLMOutput(_PayloadMapping):
    output: str = ""
    thought: str | None = None
    final_output: str | None = None

    def __post_init__(self) -> None:
        self.output = _coerce_text(self.output)
        self.thought = _coerce_optional_text(self.thought)
        self.final_output = _coerce_optional_text(self.final_output)
        if not self.output:
            if self.final_output is not None:
                self.output = self.final_output
            elif self.thought is not None:
                self.output = self.thought

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": self.output,
            "thought": self.thought,
            "final_output": self.final_output,
        }


@dataclass
class ToolInvoke(_PayloadMapping):
    tool_name: str
    arguments: dict[str, Any]
    capabilities: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "capabilities": list(self.capabilities),
        }


@dataclass
class ToolResult(_PayloadMapping):
    tool_name: str
    result: str

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "result": self.result}


RuntimePayload: TypeAlias = LLMInput | LLMOutput | ToolInvoke | ToolResult


def _redact_value(value: Any, key: str | None = None) -> Any:
    if isinstance(value, _PayloadMapping):
        return _redact_value(value.to_dict())
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
    payload: RuntimePayload
    risk_signals: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- serialization -------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "context": self.context.to_dict(),
            "payload": self.payload.to_dict(),
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
            payload=_payload_from_dict(EventType(data["event_type"]), data.get("payload") or {}),
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
            payload=_payload_from_dict(self.event_type, _redact_value(self.payload)),
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
                "payload": self.payload.to_dict(),
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
    payload: RuntimePayload,
    *,
    risk_signals: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=_new_id(),
        event_type=event_type,
        timestamp=now_ts(),
        context=context,
        payload=payload,
        risk_signals=risk_signals or [],
        metadata=metadata or {},
    )


# ---- helper constructors ----------------------------------------------
def user_input(context: RuntimeContext, text: str, **meta: Any) -> RuntimeEvent:
    """Compatibility alias: user text is now represented as LLM_INPUT."""
    return _make(
        EventType.LLM_INPUT,
        context,
        LLMInput(messages=[{"role": "user", "content": text}]),
        metadata=meta,
    )


def llm_input(context: RuntimeContext, messages: Any, **meta: Any) -> RuntimeEvent:
    return _make(EventType.LLM_INPUT, context, LLMInput(messages=_coerce_messages(messages)), metadata=meta)


def llm_output(context: RuntimeContext, output: Any, **meta: Any) -> RuntimeEvent:
    meta.setdefault("output_type", type(output).__name__)
    return _make(EventType.LLM_OUTPUT, context, _coerce_llm_output(output), metadata=meta)


def llm_thought(context: RuntimeContext, thought: str, **meta: Any) -> RuntimeEvent:
    """Compatibility alias: thoughts are no longer a separate event type."""
    text = _coerce_text(thought)
    return _make(EventType.LLM_OUTPUT, context, LLMOutput(output=text, thought=text), metadata=meta)


def tool_invoke(
    context: RuntimeContext,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    capabilities: list[str] | None = None,
    **meta: Any,
) -> RuntimeEvent:
    payload = ToolInvoke(
        tool_name=str(tool_name),
        arguments=dict(arguments or {}),
        capabilities=[str(item) for item in (capabilities or [])],
    )
    return _make(EventType.TOOL_INVOKE, context, payload, metadata=meta)


def tool_result(
    context: RuntimeContext,
    tool_name: str,
    result: Any,
    *,
    error: str | None = None,
    **meta: Any,
) -> RuntimeEvent:
    payload = ToolResult(tool_name=str(tool_name), result=_coerce_text(result))
    if error is not None:
        meta.setdefault("error", error)
    return _make(EventType.TOOL_RESULT, context, payload, metadata=meta)


def final_response(context: RuntimeContext, text: str, **meta: Any) -> RuntimeEvent:
    """Compatibility alias: final text is now represented as LLM_OUTPUT."""
    output = _coerce_text(text)
    return _make(
        EventType.LLM_OUTPUT,
        context,
        LLMOutput(output=output, final_output=output),
        metadata=meta,
    )


def _payload_from_dict(event_type: EventType, payload: Any) -> RuntimePayload:
    data = payload.to_dict() if isinstance(payload, _PayloadMapping) else dict(payload or {})
    if event_type == EventType.LLM_INPUT:
        messages = data.get("messages")
        if messages is None:
            messages = data.get("message")
        if messages is None and data.get("text") is not None:
            messages = [{"role": "user", "content": _coerce_text(data.get("text"))}]
        return LLMInput(messages=_coerce_messages(messages or []))
    if event_type == EventType.LLM_OUTPUT:
        output = data.get("output")
        if output is None:
            output = data.get("text")
        if output is None:
            output = data.get("content")
        if output is None:
            output = data.get("message")
        thought = data.get("thought")
        final_output = data.get("final_output")
        if output is None:
            output = final_output if final_output is not None else thought
        return LLMOutput(
            output=_coerce_text(output),
            thought=_coerce_optional_text(thought),
            final_output=_coerce_optional_text(final_output),
        )
    if event_type == EventType.TOOL_INVOKE:
        return ToolInvoke(
            tool_name=_coerce_text(data.get("tool_name")),
            arguments=dict(data.get("arguments") or {}),
            capabilities=[str(item) for item in (data.get("capabilities") or [])],
        )
    if event_type == EventType.TOOL_RESULT:
        return ToolResult(
            tool_name=_coerce_text(data.get("tool_name")),
            result=_coerce_text(data.get("result")),
        )
    raise ValueError(f"unsupported event type: {event_type}")


def _coerce_messages(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        messages: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                role = _coerce_text(item.get("role") or "user")
                content = _coerce_text(item.get("content"))
                msg = dict(item)
                msg["role"] = role
                msg["content"] = content
                messages.append(msg)
            else:
                messages.append({"role": "user", "content": _coerce_text(item)})
        return messages
    if isinstance(value, dict):
        return [_coerce_message_dict(value)]
    if value is None:
        return []
    return [{"role": "user", "content": _coerce_text(value)}]


def _coerce_message_dict(value: dict[str, Any]) -> dict[str, Any]:
    message = dict(value)
    message["role"] = _coerce_text(message.get("role") or "user")
    message["content"] = _coerce_text(message.get("content"))
    return message


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _coerce_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return _coerce_text(value)


def _coerce_llm_output(value: Any) -> LLMOutput:
    if isinstance(value, LLMOutput):
        return LLMOutput(
            output=value.output,
            thought=value.thought,
            final_output=value.final_output,
        )

    data = _llm_output_fields(value)
    if data is None:
        return LLMOutput(output=_coerce_text(value))

    thought = data.get("thought")
    final_output = data.get("final_output")
    output = data.get("output")
    if output is None:
        output = data.get("text")
    if output is None:
        output = data.get("content")
    if output is None:
        output = data.get("message")
    if output is None:
        output = final_output if final_output is not None else thought
    return LLMOutput(
        output=_coerce_text(output),
        thought=_coerce_optional_text(thought),
        final_output=_coerce_optional_text(final_output),
    )


def _llm_output_fields(value: Any) -> dict[str, Any] | None:
    data: dict[str, Any] | None = None
    if isinstance(value, _PayloadMapping):
        data = value.to_dict()
    elif isinstance(value, dict):
        data = dict(value)
    else:
        for method_name in ("model_dump", "to_dict", "dict"):
            dumper = getattr(value, method_name, None)
            if not callable(dumper):
                continue
            try:
                dumped = dumper()
            except TypeError:
                continue
            if isinstance(dumped, dict):
                data = dict(dumped)
                break
        if data is None:
            attrs = {
                key: getattr(value, key)
                for key in ("output", "text", "content", "message", "thought", "final_output")
                if getattr(value, key, None) is not None
            }
            data = attrs or None

    if not data:
        return None

    recognized = ("output", "text", "content", "message", "thought", "final_output")
    if not any(data.get(key) is not None for key in recognized):
        return None
    return data
