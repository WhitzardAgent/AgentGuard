"""Parse OpenAI-style function_call payloads."""
from __future__ import annotations

import json
from typing import Any

from agentguard.schemas.tool import ToolCall


def parse_function_call(obj: dict[str, Any]) -> ToolCall | None:
    """Parse an OpenAI legacy function_call dict into a ToolCall."""
    fc = obj.get("function_call") or obj
    name = fc.get("name")
    if not name:
        return None
    args = fc.get("arguments")
    arguments = _coerce_args(args)
    return ToolCall(
        tool_name=name,
        arguments=arguments,
        call_id=obj.get("id"),
        raw=obj,
        source_format="openai_function_call",
    )


def _coerce_args(args: Any) -> dict[str, Any]:
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            return parsed if isinstance(parsed, dict) else {"_raw": parsed}
        except json.JSONDecodeError:
            return {"_raw": args, "_unparsed": True}
    return {}
