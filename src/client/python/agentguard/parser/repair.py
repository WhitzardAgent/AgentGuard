"""Repair malformed or incomplete tool calls. Never repair unsafe intent."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

from agentguard.schemas.tool import ToolCall


@dataclass
class RepairResult:
    success: bool
    tool_call: ToolCall | None = None
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)


def repair_tool_call(
    call: ToolCall,
    known_tools: list[str] | None = None,
    required_args: dict[str, list[str]] | None = None,
) -> RepairResult:
    """Attempt safe, structural repair of a parsed tool call."""
    warnings: list[str] = []
    name = call.tool_name
    args = dict(call.arguments or {})

    # Repair stringified JSON arguments.
    if "_raw" in args and args.get("_unparsed"):
        try:
            parsed = json.loads(args["_raw"])
            if isinstance(parsed, dict):
                args = parsed
                warnings.append("parsed stringified JSON arguments")
        except (json.JSONDecodeError, TypeError):
            return RepairResult(False, explanation="arguments are not valid JSON")

    # Unknown tool name suggestion.
    if known_tools and name not in known_tools:
        suggestion = get_close_matches(name, known_tools, n=1)
        if suggestion:
            warnings.append(f"renamed unknown tool '{name}' -> '{suggestion[0]}'")
            name = suggestion[0]
        else:
            return RepairResult(
                False, explanation=f"unknown tool '{name}', no close match"
            )

    # Missing required arguments => cannot repair safely.
    if required_args and name in required_args:
        missing = [a for a in required_args[name] if a not in args]
        if missing:
            return RepairResult(
                False,
                explanation=f"missing required arguments: {missing}",
                warnings=warnings,
            )

    repaired = ToolCall(
        tool_name=name,
        arguments=args,
        call_id=call.call_id,
        raw=call.raw,
        source_format=call.source_format,
    )
    return RepairResult(True, tool_call=repaired, explanation="repaired", warnings=warnings)


def explain_schema_mismatch(call: ToolCall, schema: dict[str, Any]) -> str:
    props = (schema or {}).get("properties", {})
    extra = [k for k in (call.arguments or {}) if k not in props]
    missing = [k for k in props if k not in (call.arguments or {})]
    parts = []
    if missing:
        parts.append(f"missing: {missing}")
    if extra:
        parts.append(f"unexpected: {extra}")
    return "; ".join(parts) or "arguments match schema"
