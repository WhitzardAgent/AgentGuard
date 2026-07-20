"""Framework-neutral helpers for one bounded Thought-Aligner regeneration."""
from __future__ import annotations

import copy
import re
from typing import Any

from agentguard.schemas.decisions import GuardDecision


def aligned_thought(decision: GuardDecision) -> str | None:
    if decision.metadata.get("protocol") != "thought_alignment_v1":
        return None
    value = decision.metadata.get("aligned_thought")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def supports_thought_regeneration(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> bool:
    return prepare_thought_regeneration(args, kwargs, "probe") is not None


def prepare_thought_regeneration(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    safe_thought: str,
) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
    retry_args = tuple(args)
    retry_kwargs = dict(kwargs)

    if isinstance(retry_kwargs.get("agent_scratchpad"), str):
        retry_kwargs["agent_scratchpad"] = _append_aligned_thought(
            retry_kwargs["agent_scratchpad"], safe_thought
        )
        return retry_args, retry_kwargs

    for key in ("messages", "input", "prompt"):
        if key not in retry_kwargs:
            continue
        injected = _inject_thought(retry_kwargs[key], safe_thought)
        if injected is not None:
            retry_kwargs[key] = injected
            return retry_args, retry_kwargs

    if not retry_args:
        return None
    injected = _inject_thought(retry_args[0], safe_thought)
    if injected is None:
        return None
    return (injected, *retry_args[1:]), retry_kwargs


def merge_aligned_thought(regenerated: Any, original: Any, safe_thought: str) -> Any:
    """Preserve common native response shapes while recording the effective thought."""
    if isinstance(regenerated, str):
        if not _has_react_action(original) and not _has_react_action(regenerated):
            return regenerated
        match = _REACT_CONTINUATION_RE.search(regenerated)
        continuation = match.group("body").strip() if match else regenerated.strip()
        return f"Thought: {safe_thought}\n{continuation}"

    if isinstance(regenerated, dict):
        merged = copy.deepcopy(regenerated)
        merged["thought"] = safe_thought
        additional = merged.get("additional_kwargs")
        if isinstance(additional, dict):
            additional["reasoning_content"] = safe_thought
        return merged

    model_copy = getattr(regenerated, "model_copy", None)
    if callable(model_copy):
        additional = getattr(regenerated, "additional_kwargs", None)
        if isinstance(additional, dict):
            updated = dict(additional)
            updated["reasoning_content"] = safe_thought
            try:
                return model_copy(update={"additional_kwargs": updated})
            except Exception:
                pass
    return regenerated


def _inject_thought(value: Any, safe_thought: str) -> Any | None:
    if isinstance(value, str):
        return _append_aligned_thought(value, safe_thought)

    if isinstance(value, dict):
        cloned = dict(value)
        if isinstance(cloned.get("agent_scratchpad"), str):
            cloned["agent_scratchpad"] = _append_aligned_thought(
                cloned["agent_scratchpad"], safe_thought
            )
            return cloned
        for key in ("messages", "input", "prompt"):
            if key not in cloned:
                continue
            injected = _inject_thought(cloned[key], safe_thought)
            if injected is not None:
                cloned[key] = injected
                return cloned
        return None

    if isinstance(value, (list, tuple)):
        messages = list(value)
        additions = _thought_messages(messages, safe_thought)
        if additions is None:
            return None
        combined = [*messages, *additions]
        return tuple(combined) if isinstance(value, tuple) else combined
    return None


def _thought_messages(messages: list[Any], safe_thought: str) -> list[Any] | None:
    directive = _action_only_directive()
    if not messages or all(isinstance(item, dict) for item in messages):
        return [
            {"role": "assistant", "content": f"Thought: {safe_thought}"},
            {"role": "user", "content": directive},
        ]
    if all(isinstance(item, str) for item in messages):
        return [f"Thought: {safe_thought}\n{directive}"]

    try:
        from langchain_core.messages import AIMessage, HumanMessage
    except Exception:
        return None
    if not all(
        hasattr(item, "content") and "langchain" in type(item).__module__.lower()
        for item in messages
    ):
        return None
    return [AIMessage(content=f"Thought: {safe_thought}"), HumanMessage(content=directive)]


def _append_aligned_thought(value: str, safe_thought: str) -> str:
    prefix = value.rstrip()
    if prefix:
        prefix += "\n"
    return f"{prefix}Thought: {safe_thought}\n\n{_action_only_directive()}"


def _action_only_directive() -> str:
    return (
        "(**DO NOT generate 'Thought' again! Output 'Action' and 'Action Input', "
        "or 'Final Answer', directly. If user confirmation is required, output "
        "the 'Final Answer' directly and do not generate 'Action'.**)"
    )


_REACT_CONTINUATION_RE = re.compile(
    r"(?:^|\n)\s*(?P<body>(?:Action(?:\s+Input)?|Final\s+Answer)\s*:.*)\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


def _has_react_action(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.search(r"(?:^|\n)\s*Action\s*:", value, flags=re.IGNORECASE))
    if isinstance(value, dict):
        for key in ("output", "text", "content", "message"):
            if _has_react_action(value.get(key)):
                return True
    return False


__all__ = [
    "aligned_thought",
    "merge_aligned_thought",
    "prepare_thought_regeneration",
    "supports_thought_regeneration",
]
