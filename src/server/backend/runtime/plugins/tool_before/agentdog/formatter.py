"""Format AgentGuard runtime events as AgentDog trajectory text."""
from __future__ import annotations

from dataclasses import dataclass
import ast
import json
import re
from typing import Any

from shared.schemas.events import RuntimeEvent


@dataclass(frozen=True)
class FormattedAgentDogTrajectory:
    trajectory: str
    tool_list: list[str]
    trajectory_chars: int


def format_agentdog_trajectory(
    events: list[RuntimeEvent],
    *,
    max_chars: int = 24000,
) -> FormattedAgentDogTrajectory:
    profile_lines: list[str] = []
    history_lines: list[str] = []
    tools: set[str] = set()

    for event in _dedupe_events(events):
        event_type = event.event_type.value
        if event_type == "llm_input":
            _append_llm_input(event.payload.to_dict(), profile_lines, history_lines)
        elif event_type == "llm_output":
            _append_llm_output(event.payload.to_dict(), history_lines)
        elif event_type == "tool_invoke":
            tool_name = _append_tool_invoke(event.payload.to_dict(), history_lines)
            if tool_name:
                tools.add(tool_name)
        elif event_type == "tool_result":
            tool_name = _append_tool_result(event.payload.to_dict(), event.metadata, history_lines)
            if tool_name:
                tools.add(tool_name)

    sections: list[str] = []
    if profile_lines:
        sections.append("=== Agent Profile ===\n" + "\n".join(profile_lines))
    sections.append("=== Conversation History ===")
    if history_lines:
        sections.append("\n\n".join(history_lines))
    trajectory = "\n\n".join(sections).strip()
    trajectory = _truncate_from_front(trajectory, max_chars=max_chars)
    return FormattedAgentDogTrajectory(
        trajectory=trajectory,
        tool_list=sorted(tools),
        trajectory_chars=len(trajectory),
    )


def _dedupe_events(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    seen: set[str] = set()
    out: list[RuntimeEvent] = []
    for event in events:
        event_id = str(getattr(event, "event_id", "") or "")
        if event_id and event_id in seen:
            continue
        if event_id:
            seen.add(event_id)
        out.append(event)
    return out


def _append_llm_input(
    payload: dict[str, Any],
    profile_lines: list[str],
    history_lines: list[str],
) -> None:
    for block in payload.get("messages") or []:
        for item in _iter_message_items(block):
            _append_message_item(item, profile_lines, history_lines)


def _iter_message_items(block: Any) -> list[Any]:
    if isinstance(block, dict) and isinstance(block.get("input"), list):
        return list(block.get("input") or [])
    return [block]


def _append_message_item(
    item: Any,
    profile_lines: list[str],
    history_lines: list[str],
) -> None:
    if not isinstance(item, dict):
        text = _extract_text(item)
        if text:
            history_lines.append(f"[USER] {text}")
        return

    role_raw: Any
    content: Any
    tool_name = _tool_name_from_message(item)
    if isinstance(item.get("data"), dict):
        role_raw = item.get("type")
        content = item["data"].get("content")
        tool_name = tool_name or _tool_name_from_message(item["data"])
    else:
        role_raw = item.get("role") or item.get("type")
        content = item.get("content")

    text = _extract_text(content)
    if not text:
        return

    role = _normalized_role(role_raw)
    if role in {"system", "developer"}:
        profile_lines.append(f"{role}: {text}")
    elif role == "user":
        history_lines.append(f"[USER] {text}")
    elif role == "assistant":
        history_lines.append(f"[ASSISTANT] {text}")
    elif role == "tool":
        history_lines.append(f"[TOOL_RESULT: {tool_name or 'tool'}] {text}")
    else:
        history_lines.append(f"[{_sanitize_role_label(role_raw)}] {text}")


def _append_llm_output(payload: dict[str, Any], history_lines: list[str]) -> None:
    thought = _extract_text(payload.get("thought"))
    final_output = _extract_text(payload.get("final_output"))
    if thought or final_output:
        if thought:
            history_lines.append(f"[THINKING] {thought}")
        if final_output:
            history_lines.append(f"[ASSISTANT] {final_output}")
        return

    structured = _parse_structured_payload(payload.get("output"))
    if isinstance(structured, dict):
        data = structured.get("data") if isinstance(structured.get("data"), dict) else structured
        content = _extract_text(data.get("content") if isinstance(data, dict) else None)
        if content:
            history_lines.append(f"[ASSISTANT] {content}")
        elif not (isinstance(data, dict) and data.get("tool_calls")):
            raw = _extract_text(payload.get("output"))
            if raw:
                history_lines.append(f"[ASSISTANT] {raw}")
        return

    text = _extract_text(payload.get("output"))
    if text:
        history_lines.append(f"[ASSISTANT] {text}")


def _append_tool_invoke(payload: dict[str, Any], history_lines: list[str]) -> str:
    tool_name = str(payload.get("tool_name") or "tool")
    args = _json_text(payload.get("arguments") or {})
    history_lines.append(f"[TOOL_CALL: {tool_name}] {args}")
    return tool_name


def _append_tool_result(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    history_lines: list[str],
) -> str:
    tool_name = str(payload.get("tool_name") or "tool")
    error = metadata.get("error")
    if error not in (None, ""):
        history_lines.append(f"[TOOL_RESULT: {tool_name} [ERROR]] {_extract_text(error)}")
    else:
        history_lines.append(f"[TOOL_RESULT: {tool_name}] {_extract_text(payload.get('result'))}")
    return tool_name


def _normalized_role(role: Any) -> str:
    value = str(role or "").strip()
    low = value.lower()
    if low.startswith("messagerole."):
        low = low.rsplit(".", 1)[-1]
    aliases = {
        "human": "user",
        "humanmessage": "user",
        "user": "user",
        "ai": "assistant",
        "aimessage": "assistant",
        "assistant": "assistant",
        "system": "system",
        "systemmessage": "system",
        "developer": "developer",
        "tool": "tool",
        "toolmessage": "tool",
        "toolresult": "tool",
        "environment": "tool",
    }
    return aliases.get(low, low)


def _sanitize_role_label(role: Any) -> str:
    value = str(role or "unknown").strip()
    value = re.sub(r"[\[\]\r\n]+", "_", value)
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "_", value)
    value = value.strip("_.:-")
    return (value or "unknown").upper()


def _tool_name_from_message(item: dict[str, Any]) -> str:
    for key in ("tool_name", "toolName", "name", "id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    if isinstance(content, dict):
        for key in ("text", "content"):
            value = content.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return _json_text(content)
    return str(content).strip()


def _parse_structured_payload(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _truncate_from_front(text: str, *, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = "[TRUNCATED]\n"
    return marker + text[-max(max_chars - len(marker), 0):]
