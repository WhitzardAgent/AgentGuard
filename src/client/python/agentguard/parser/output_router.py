"""Route raw LLM output into a classified category."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from agentguard.plugins.common.patterns import find_signals, text_of
from agentguard.parser.tool_call_parser import parse_tool_calls
from agentguard.schemas.tool import ToolCall


class OutputKind(str, Enum):
    TEXT_OUTPUT = "text_output"
    TOOL_CALL_CANDIDATE = "tool_call_candidate"
    MALFORMED_TOOL_CALL = "malformed_tool_call"
    UNSAFE_OUTPUT = "unsafe_output"


@dataclass
class RouterResult:
    kind: OutputKind
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    risk_signals: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    raw: Any = None


_TOOL_KEYS = ("tool_calls", "function_call", "tool", "tool_name")


def route_output(output: Any) -> RouterResult:
    """Classify an LLM output. Avoid sending plain text to the tool parser."""
    # 1. dict outputs: inspect structure before parsing tools.
    if isinstance(output, dict):
        if output.get("type") == "tool_use" or any(k in output for k in _TOOL_KEYS):
            return _route_tool(output)
        text = (
            output.get("text")
            or output.get("content")
            or output.get("output")
            or output.get("final_output")
            or output.get("thought")
        )
        return _route_text(text_of(text if text is not None else output), raw=output)

    if isinstance(output, list):
        # Anthropic-style content blocks.
        if any(isinstance(b, dict) and b.get("type") == "tool_use" for b in output):
            return _route_tool(output)
        return _route_text(text_of(output), raw=output)

    if isinstance(output, str):
        stripped = output.strip()
        if stripped.startswith("{") and any(k in stripped for k in _TOOL_KEYS):
            return _route_tool(output)
        return _route_text(output, raw=output)

    return _route_text(text_of(output), raw=output)


def _route_tool(output: Any) -> RouterResult:
    parsed = parse_tool_calls(output)
    if parsed.malformed and not parsed.tool_calls:
        return RouterResult(
            OutputKind.MALFORMED_TOOL_CALL, errors=parsed.errors, raw=output
        )
    if not parsed.tool_calls:
        return _route_text(text_of(output), raw=output)
    signals: list[str] = []
    for tc in parsed.tool_calls:
        signals.extend(find_signals(text_of(tc.arguments)))
    return RouterResult(
        OutputKind.TOOL_CALL_CANDIDATE,
        tool_calls=parsed.tool_calls,
        risk_signals=sorted(set(signals)),
        errors=parsed.errors,
        raw=output,
    )


def _route_text(text: str, raw: Any = None) -> RouterResult:
    signals = find_signals(text)
    unsafe = {"secret_detected", "api_key_detected", "system_prompt_leak"} & set(signals)
    kind = OutputKind.UNSAFE_OUTPUT if unsafe else OutputKind.TEXT_OUTPUT
    return RouterResult(kind, text=text, risk_signals=signals, raw=raw)
