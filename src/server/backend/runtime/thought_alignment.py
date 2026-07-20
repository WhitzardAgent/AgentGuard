"""Thought-Aligner context extraction and OpenAI-compatible transport."""
from __future__ import annotations

import json
import re
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from shared.schemas.context import RuntimeContext
from shared.schemas.events import EventType, RuntimeEvent

_THOUGHT_KEYS = (
    "thought",
    "reasoning_content",
    "reasoningContent",
    "reasoning",
    "thinking",
    "plan",
    "analysis",
)
_INSTRUCTION_KEYS = (
    "instruction",
    "user_instruction",
    "task_instruction",
    "goal",
    "task",
)
_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning|analysis)\b[^>]*>"
    r"(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_REACT_THOUGHT_RE = re.compile(
    r"(?:^|\n)\s*(?:Thought|Reasoning|Analysis|思考)\s*:\s*(?P<body>.*?)"
    r"(?=\n\s*(?:Action(?:\s+Input)?|Observation|Final\s+Answer|Answer|"
    r"行动|观察|最终答案)\s*:|\Z)",
    flags=re.IGNORECASE | re.DOTALL,
)
_RENDERED_USER_RE = re.compile(
    r"(?:^|\n)\s*(?:Human|User|用户)\s*:\s*(?P<body>.*?)"
    r"(?=\n\s*(?:AI|Assistant|System|Human|User|工具|观察)\s*:|\Z)",
    flags=re.IGNORECASE | re.DOTALL,
)
_OFFICIAL_PROMPT = (
    "BEGINNING OF CONVERSATION: USER: Edit the following Instruction-Thought pair "
    "to make it more helpful and harmless. : {instruction} | {thought} ASSISTANT:"
)


class ThoughtAlignmentError(RuntimeError):
    """Raised when a configured Thought-Aligner endpoint cannot produce a thought."""


@dataclass(frozen=True)
class ThoughtObservation:
    thought: str
    observation: str


@dataclass(frozen=True)
class AlignmentContext:
    instruction: str
    thought: str
    history: tuple[ThoughtObservation, ...] = ()

    @property
    def formatted_instruction(self) -> str:
        parts = [self.instruction]
        for item in self.history:
            parts.append(f"<thought> {_escape_marker_text(item.thought)} </thought>")
            parts.append(
                f"<observation> {_escape_marker_text(item.observation)} </observation>"
            )
        return "\n".join(part for part in parts if part).strip()


def build_alignment_context(
    event: RuntimeEvent,
    context: RuntimeContext,
    trajectory_window: list[RuntimeEvent] | None,
    *,
    max_instruction_chars: int = 12_000,
    max_thought_chars: int = 8_000,
    max_observation_chars: int = 12_000,
    max_history_items: int = 8,
) -> AlignmentContext | None:
    """Build the model input without depending on framework-specific classes."""
    if event.event_type != EventType.LLM_OUTPUT:
        return None

    thought = extract_thought(event.payload)
    if not thought:
        return None

    trace = list(trajectory_window or [])
    instruction = _extract_instruction(event, context, trace)
    if not instruction:
        return None

    history = _extract_history(trace)
    history_limit = max(0, int(max_history_items))
    selected_history = history[-history_limit:] if history_limit else []
    bounded_history = tuple(
        ThoughtObservation(
            thought=_clip(item.thought, max_thought_chars),
            observation=_clip(item.observation, max_observation_chars),
        )
        for item in selected_history
    )
    return AlignmentContext(
        instruction=_clip(instruction, max_instruction_chars),
        thought=_clip(thought, max_thought_chars),
        history=bounded_history,
    )


def extract_thought(value: Any) -> str | None:
    """Extract exposed reasoning from normalized fields, tags, or ReAct text."""
    explicit = _find_reasoning_value(value, top_level_only=True)
    if explicit:
        return explicit

    nested = _find_reasoning_value(value, top_level_only=False)
    if nested:
        return nested

    for text in _candidate_output_texts(value):
        tagged = _extract_tagged_thought(text)
        if tagged:
            return tagged
        react = _extract_react_thought(text)
        if react:
            return react
    return None


class ThoughtAlignerClient:
    """Small dedicated client for an OpenAI-compatible Thought-Aligner endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        timeout_s: float = 30.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.model = str(model or "").strip()
        self.timeout_s = float(timeout_s)
        self._opener = opener or urllib.request.urlopen

    def align(self, instruction: str, thought: str) -> str:
        if not self.base_url or not self.api_key or not self.model:
            raise ThoughtAlignmentError("Thought-Aligner configuration is incomplete")

        prompt = _OFFICIAL_PROMPT.format(instruction=instruction, thought=thought)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            _chat_completions_url(self.base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout_s) as response:
                decoded = json.loads(response.read().decode("utf-8"))
            content = decoded["choices"][0]["message"]["content"]
        except ThoughtAlignmentError:
            raise
        except Exception as exc:
            raise ThoughtAlignmentError("Thought-Aligner request or response failed") from exc

        if not isinstance(content, str) or not content.strip():
            raise ThoughtAlignmentError("Thought-Aligner response did not contain text")
        return _clean_aligned_thought(content)


def _extract_instruction(
    event: RuntimeEvent,
    context: RuntimeContext,
    trace: list[RuntimeEvent],
) -> str | None:
    for source in (event.metadata, context.metadata):
        explicit = _first_text_for_keys(source, _INSTRUCTION_KEYS)
        if explicit:
            return explicit

    for candidate in reversed(trace):
        if candidate.event_type != EventType.LLM_INPUT:
            continue
        instruction = _extract_user_instruction(candidate.payload)
        if instruction:
            return instruction
    return None


def _extract_user_instruction(value: Any) -> str | None:
    normalized = _mapping_or_attributes(value)
    if normalized is not None:
        messages = normalized.get("messages")
        if messages is not None:
            role_text = _last_role_text(messages)
            if role_text:
                return role_text
            nested = _extract_user_instruction(messages)
            if nested:
                return nested

        role = str(normalized.get("role") or normalized.get("type") or "").lower()
        content = _as_text(normalized.get("content"))
        if role in {"user", "human"} and content:
            return _rendered_user_or_text(content)

        for key in ("input", "prompt", "query", "request", "text", "content"):
            if key not in normalized:
                continue
            nested = _extract_user_instruction(normalized[key])
            if nested:
                return nested
        return None

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        role_text = _last_role_text(value)
        if role_text:
            return role_text
        for item in reversed(value):
            nested = _extract_user_instruction(item)
            if nested:
                return nested
        return None

    text = _as_text(value)
    return _rendered_user_or_text(text) if text else None


def _last_role_text(value: Any) -> str | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    for item in reversed(value):
        mapping = _mapping_or_attributes(item)
        if mapping is None:
            continue
        role = str(mapping.get("role") or mapping.get("type") or "").lower()
        if role not in {"user", "human"}:
            continue
        content = _as_text(mapping.get("content"))
        if content:
            return _rendered_user_or_text(content)
    return None


def _rendered_user_or_text(text: str) -> str:
    matches = list(_RENDERED_USER_RE.finditer(text))
    if matches:
        return matches[-1].group("body").strip()
    return text.strip()


def _extract_history(trace: list[RuntimeEvent]) -> list[ThoughtObservation]:
    history: list[ThoughtObservation] = []
    pending_thought: str | None = None
    observations: list[Any] = []

    def flush() -> None:
        nonlocal pending_thought, observations
        if pending_thought and observations:
            observation_value: Any = observations[0] if len(observations) == 1 else observations
            history.append(
                ThoughtObservation(
                    thought=pending_thought,
                    observation=_serialize_observation(observation_value),
                )
            )
        pending_thought = None
        observations = []

    for item in trace:
        if item.event_type == EventType.LLM_OUTPUT:
            flush()
            pending_thought = extract_thought(item.payload)
        elif item.event_type == EventType.TOOL_RESULT and pending_thought:
            observations.append(item.payload.get("result"))
    flush()
    return history


def _find_reasoning_value(value: Any, *, top_level_only: bool) -> str | None:
    mapping = _mapping_or_attributes(value)
    if mapping is None:
        return None

    direct = _first_text_for_keys(mapping, _THOUGHT_KEYS)
    if direct:
        return direct
    if top_level_only:
        return None

    seen: set[int] = set()

    def walk(item: Any, depth: int) -> str | None:
        if depth > 5 or id(item) in seen:
            return None
        if isinstance(item, (Mapping, list, tuple)):
            seen.add(id(item))
        nested_mapping = _mapping_or_attributes(item)
        if nested_mapping is not None:
            found = _first_text_for_keys(nested_mapping, _THOUGHT_KEYS)
            if found:
                return found
            for nested_value in nested_mapping.values():
                found = walk(nested_value, depth + 1)
                if found:
                    return found
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for nested_value in item:
                found = walk(nested_value, depth + 1)
                if found:
                    return found
        return None

    return walk(value, 0)


def _candidate_output_texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    mapping = _mapping_or_attributes(value)
    if mapping is None:
        return []
    values: list[str] = []
    for key in ("output", "text", "content", "message", "final_output"):
        text = _as_text(mapping.get(key))
        if text:
            values.append(text)
    return values


def _mapping_or_attributes(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            dumped = to_dict()
        except Exception:
            dumped = None
        if isinstance(dumped, Mapping):
            return dict(dumped)
    attributes = {
        key: getattr(value, key)
        for key in (
            *_THOUGHT_KEYS,
            "output",
            "text",
            "content",
            "message",
            "final_output",
            "messages",
            "role",
            "type",
        )
        if getattr(value, key, None) is not None
    }
    return attributes or None


def _first_text_for_keys(value: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        text = _as_text(value.get(key))
        if text:
            return text
    return None


def _as_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
                continue
            mapping = _mapping_or_attributes(item)
            if mapping is None:
                continue
            text = _first_text_for_keys(mapping, ("text", "content", "summary"))
            if text:
                parts.append(text)
        joined = "\n".join(part.strip() for part in parts if part.strip())
        return joined or None
    return None


def _extract_tagged_thought(text: str) -> str | None:
    parts = [match.group("body").strip() for match in _THOUGHT_TAG_RE.finditer(text)]
    return "\n\n".join(part for part in parts if part) or None


def _extract_react_thought(text: str) -> str | None:
    match = _REACT_THOUGHT_RE.search(text)
    return match.group("body").strip() or None if match else None


def _serialize_observation(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _clip(value: str, limit: int) -> str:
    cleaned = value.strip()
    bounded = max(1, int(limit))
    if len(cleaned) <= bounded:
        return cleaned
    return cleaned[:bounded]


def _escape_marker_text(value: str) -> str:
    return re.sub(
        r"</?(?:thought|observation)>",
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        value,
        flags=re.IGNORECASE,
    )


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def _clean_aligned_thought(value: str) -> str:
    cleaned = value.strip()
    tagged = _extract_tagged_thought(cleaned)
    if tagged and _THOUGHT_TAG_RE.fullmatch(cleaned):
        return tagged
    return re.sub(r"^\s*Thought\s*:\s*", "", cleaned, count=1, flags=re.IGNORECASE).strip()


__all__ = [
    "AlignmentContext",
    "ThoughtAlignerClient",
    "ThoughtAlignmentError",
    "ThoughtObservation",
    "build_alignment_context",
    "extract_thought",
]
