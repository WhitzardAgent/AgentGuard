"""LlamaIndex agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

import functools
import inspect
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from agentguard.adapters.agent.base import BaseAgentAdapter, LLMBinding
from agentguard.adapters.agent.normalization import (
    LLMInputNormalization,
    LLMOutputNormalization,
    ToolInvokeNormalization,
    ToolResultNormalization,
)
from agentguard.adapters.agent.patching import is_guarded, mark_guarded, set_attr
from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.tools.metadata import ToolMetadata
from agentguard.utils.errors import AdapterError

_LLM_METHODS = (
    "chat",
    "achat",
    "complete",
    "acomplete",
    "stream_chat",
    "astream_chat",
    "stream_complete",
    "astream_complete",
)
_STREAM_METHODS = {
    "stream_chat",
    "astream_chat",
    "stream_complete",
    "astream_complete",
}


class LlamaIndexAgentAdapter(BaseAgentAdapter):
    name = "llamaindex"

    def __init__(self) -> None:
        super().__init__()
        self._tool_metadata_cache: dict[tuple[int, str], ToolMetadata] = {}

    def can_wrap(self, agent: Any) -> bool:
        mod = type(agent).__module__ or ""
        if "llama_index" in mod or "llamaindex" in mod:
            return True
        return any(_looks_like_workflow_agent(item) for item in _iter_workflow_agents(agent))

    def attach(
        self,
        agent: Any,
        guard: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        self._tool_metadata_cache = {}
        return super().attach(agent, guard, wrap_tools=wrap_tools, wrap_llm=wrap_llm)

    def gettools(self, agent: Any):
        _ = agent
        return []

    def patchtool(self, agent: Any, guard: Any) -> int:
        patched = 0
        for workflow_agent in _iter_workflow_agents(agent):
            original = getattr(workflow_agent, "_call_tool", None)
            if not callable(original) or is_guarded(original):
                continue
            wrapped = _make_guarded_call_tool(self, guard, workflow_agent, original)
            if set_attr(workflow_agent, "_call_tool", wrapped):
                patched += 1
        return patched

    def getllm(self, agent: Any) -> list[LLMBinding]:
        bindings: list[LLMBinding] = []
        seen: set[int] = set()
        for workflow_agent in _iter_workflow_agents(agent):
            llm = getattr(workflow_agent, "llm", None)
            if llm is None or id(llm) in seen:
                continue
            seen.add(id(llm))
            for label in _LLM_METHODS:
                fn = getattr(llm, label, None)
                if not callable(fn) or is_guarded(fn):
                    continue
                bindings.append(
                    self.build_llm_binding(
                        label=label,
                        fn=fn,
                        owner=llm,
                        attr=label,
                        installer=_install_llamaindex_llm_binding,
                    )
                )
        return bindings

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("chat", "query", "run"):
            fn = getattr(agent, method, None)
            if callable(fn):
                try:
                    return str(fn(prompt))
                except Exception as exc:
                    raise AdapterError(f"llamaindex agent call failed: {exc}") from exc
        raise AdapterError("llamaindex agent exposes no chat/query/run")

    def normalize_llm_input(
        self,
        *,
        label: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> LLMInputNormalization:
        _ = fn
        payload: dict[str, Any] = {"label": label}
        if args:
            payload["input"] = _normalize_llamaindex_value(args[0])
            if len(args) > 1:
                payload["args"] = _normalize_llamaindex_value(list(args[1:]))
        if kwargs:
            payload["kwargs"] = _normalize_llamaindex_value(dict(kwargs))
        return LLMInputNormalization(
            payload=payload,
            metadata=_llamaindex_meta(label=label, owner=owner),
        )

    def normalize_llm_output(
        self,
        *,
        label: str,
        output: Any,
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> LLMOutputNormalization:
        _ = fn
        return LLMOutputNormalization(
            payload=_normalize_llamaindex_llm_output(output),
            metadata=_llamaindex_meta(label=label, owner=owner),
        )

    def normalize_tool_invoke(
        self,
        *,
        tool_metadata: ToolMetadata,
        arguments: dict[str, Any],
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> ToolInvokeNormalization:
        _ = fn
        normalized = _normalize_llamaindex_value(arguments)
        if not isinstance(normalized, dict):
            normalized = {"input": normalized}
        return ToolInvokeNormalization(
            arguments=normalized,
            capabilities=list(tool_metadata.capabilities),
            metadata=_llamaindex_meta(owner=owner),
        )

    def normalize_tool_result(
        self,
        *,
        tool_name: str,
        result: Any = None,
        error: str | None = None,
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> ToolResultNormalization:
        _ = (tool_name, fn)
        return ToolResultNormalization(
            result=_normalize_llamaindex_value(result),
            error=error,
            metadata=_llamaindex_meta(owner=owner),
        )

    def tool_metadata_for(self, guard: Any, tool: Any, original: Callable[..., Any]) -> ToolMetadata:
        name = _tool_name(tool)
        key = (id(tool), name)
        cached = self._tool_metadata_cache.get(key)
        if cached is not None:
            return cached

        li_meta = getattr(tool, "metadata", None)
        schema = _tool_schema(li_meta)
        metadata = guard.register_tool(
            original,
            metadata=ToolMetadata(
                name=name,
                description=str(getattr(li_meta, "description", "") or ""),
                capabilities=list(getattr(tool, "capabilities", None) or []),
                required_args=_required_args_from_schema(schema),
                is_async=inspect.iscoroutinefunction(original),
                schema=schema,
                metadata={
                    "adapter": self.name,
                    "return_direct": bool(getattr(li_meta, "return_direct", False)),
                    "owner_type": type(tool).__name__,
                    "owner_module": type(tool).__module__,
                },
            ),
        )
        self._tool_metadata_cache[key] = metadata
        return metadata


def _make_guarded_call_tool(
    adapter: LlamaIndexAgentAdapter,
    guard: Any,
    workflow_agent: Any,
    original: Callable[..., Any],
) -> Callable[..., Any]:
    @functools.wraps(original)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        tool, tool_input = _extract_call_tool_args(args, kwargs)
        metadata = adapter.tool_metadata_for(guard, tool, original)
        arguments = tool_input if isinstance(tool_input, dict) else {"input": tool_input}
        try:
            invoke = adapter.normalize_tool_invoke(
                tool_metadata=metadata,
                arguments=arguments,
                fn=original,
                owner=tool,
            )
            decision = guard.runtime.guard(
                ev.tool_invoke(
                    guard.context,
                    metadata.name,
                    dict(invoke.arguments),
                    capabilities=list(invoke.capabilities or metadata.capabilities),
                    **dict(invoke.metadata),
                )
            ).decision
            blocked = _blocked_tool_output(decision, metadata.name, arguments)
            if blocked is not None:
                return blocked

            try:
                value = original(*args, **kwargs)
                if inspect.isawaitable(value):
                    value = await value
            except Exception as exc:
                adapter.normalize_tool_result(
                    tool_name=metadata.name,
                    result=None,
                    error=str(exc),
                    fn=original,
                    owner=tool,
                )
                guard.runtime.guard(
                    ev.tool_result(
                        guard.context,
                        metadata.name,
                        None,
                        error=str(exc),
                        **_llamaindex_meta(owner=tool),
                    ),
                    phase="after",
                )
                raise

            result = adapter.normalize_tool_result(
                tool_name=metadata.name,
                result=value,
                fn=original,
                owner=tool,
            )
            result_decision = guard.runtime.guard(
                ev.tool_result(
                    guard.context,
                    metadata.name,
                    result.result,
                    error=result.error,
                    **dict(result.metadata),
                ),
                phase="after",
            ).decision
            result_blocked = _blocked_result_output(
                result_decision,
                metadata.name,
                arguments,
            )
            return result_blocked if result_blocked is not None else value
        except Exception:
            guard.runtime.sync_local_cache_now(reason="client_error")
            raise
        finally:
            guard.runtime.sync_local_cache_async(reason="round_complete")

    return mark_guarded(wrapper)


def _install_llamaindex_llm_binding(
    guard: Any,
    binding: LLMBinding,
    adapter: LlamaIndexAgentAdapter,
) -> int:
    owner = binding.owner
    attr = binding.attr or binding.label
    fn = binding.callable
    if owner is None or not callable(fn) or is_guarded(fn):
        return 0
    wrapped = _make_guarded_llm_callable(
        guard,
        fn,
        label=binding.label,
        adapter=adapter,
        owner=owner,
        stream=binding.label in _STREAM_METHODS,
    )
    return 1 if set_attr(owner, attr, wrapped) else 0


def _make_guarded_llm_callable(
    guard: Any,
    fn: Callable[..., Any],
    *,
    label: str,
    adapter: LlamaIndexAgentAdapter,
    owner: Any = None,
    stream: bool = False,
) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                decision = _guard_llm_before(guard, adapter, label, args, kwargs, fn, owner)
                blocked = _blocked_llm_value(decision)
                if blocked is not None:
                    return blocked
                raw = await fn(*args, **kwargs)
                if stream:
                    return _wrap_async_stream(guard, adapter, label, raw, fn, owner)
                decision = _guard_llm_after(guard, adapter, label, raw, fn, owner)
                blocked = _blocked_llm_value(decision)
                return blocked if blocked is not None else raw
            except Exception:
                guard.runtime.sync_local_cache_now(reason="client_error")
                raise
            finally:
                guard.runtime.sync_local_cache_async(reason="round_complete")

        return mark_guarded(async_wrapper)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            decision = _guard_llm_before(guard, adapter, label, args, kwargs, fn, owner)
            blocked = _blocked_llm_value(decision)
            if blocked is not None:
                return blocked
            raw = fn(*args, **kwargs)
            if stream:
                return _wrap_sync_stream(guard, adapter, label, raw, fn, owner)
            decision = _guard_llm_after(guard, adapter, label, raw, fn, owner)
            blocked = _blocked_llm_value(decision)
            return blocked if blocked is not None else raw
        except Exception:
            guard.runtime.sync_local_cache_now(reason="client_error")
            raise
        finally:
            guard.runtime.sync_local_cache_async(reason="round_complete")

    return mark_guarded(wrapper)


def _guard_llm_before(
    guard: Any,
    adapter: LlamaIndexAgentAdapter,
    label: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    fn: Callable[..., Any],
    owner: Any,
) -> GuardDecision:
    normalized = adapter.normalize_llm_input(
        label=label,
        args=args,
        kwargs=kwargs,
        fn=fn,
        owner=owner,
    )
    return guard.runtime.guard(
        ev.llm_input(guard.context, normalized.payload, **dict(normalized.metadata))
    ).decision


def _guard_llm_after(
    guard: Any,
    adapter: LlamaIndexAgentAdapter,
    label: str,
    output: Any,
    fn: Callable[..., Any],
    owner: Any,
) -> GuardDecision:
    normalized = adapter.normalize_llm_output(label=label, output=output, fn=fn, owner=owner)
    return guard.runtime.guard(
        ev.llm_output(guard.context, normalized.payload, **dict(normalized.metadata)),
        phase="after",
    ).decision


def _wrap_sync_stream(
    guard: Any,
    adapter: LlamaIndexAgentAdapter,
    label: str,
    stream: Any,
    fn: Callable[..., Any],
    owner: Any,
) -> Iterator[Any]:
    def generator() -> Iterator[Any]:
        last = None
        count = 0
        try:
            for chunk in stream:
                last = chunk
                count += 1
                yield chunk
            output = last if last is not None else {"streamed_chunks": count}
            _guard_llm_after(guard, adapter, label, output, fn, owner)
        except Exception:
            guard.runtime.sync_local_cache_now(reason="client_error")
            raise
        finally:
            guard.runtime.sync_local_cache_async(reason="stream_complete")

    return generator()


def _wrap_async_stream(
    guard: Any,
    adapter: LlamaIndexAgentAdapter,
    label: str,
    stream: Any,
    fn: Callable[..., Any],
    owner: Any,
) -> AsyncIterator[Any]:
    async def generator() -> AsyncIterator[Any]:
        last = None
        count = 0
        try:
            async for chunk in stream:
                last = chunk
                count += 1
                yield chunk
            output = last if last is not None else {"streamed_chunks": count}
            _guard_llm_after(guard, adapter, label, output, fn, owner)
        except Exception:
            guard.runtime.sync_local_cache_now(reason="client_error")
            raise
        finally:
            guard.runtime.sync_local_cache_async(reason="stream_complete")

    return generator()


def _iter_workflow_agents(agent: Any) -> list[Any]:
    found: list[Any] = []
    seen: set[int] = set()

    def add(candidate: Any) -> None:
        if candidate is None or id(candidate) in seen:
            return
        seen.add(id(candidate))
        if _looks_like_workflow_agent(candidate):
            found.append(candidate)

    add(agent)
    agents = getattr(agent, "agents", None)
    if isinstance(agents, dict):
        iterable = agents.values()
    elif isinstance(agents, (list, tuple, set)):
        iterable = agents
    else:
        iterable = []
    for item in iterable:
        add(item)
    return found


def _looks_like_workflow_agent(agent: Any) -> bool:
    if agent is None:
        return False
    if callable(getattr(agent, "_call_tool", None)):
        return True
    return getattr(agent, "llm", None) is not None and (
        hasattr(agent, "tools") or callable(getattr(agent, "get_tools", None))
    )


def _extract_call_tool_args(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any, Any]:
    tool = kwargs.get("tool")
    tool_input = kwargs.get("tool_input")
    if tool is None and len(args) >= 2:
        tool = args[1]
    if tool_input is None and len(args) >= 3:
        tool_input = args[2]
    if tool is None:
        raise TypeError("llamaindex _call_tool wrapper could not find tool argument")
    if tool_input is None:
        tool_input = {}
    return tool, tool_input


def _tool_name(tool: Any) -> str:
    metadata = getattr(tool, "metadata", None)
    get_name = getattr(metadata, "get_name", None)
    if callable(get_name):
        try:
            return str(get_name())
        except Exception:
            pass
    return str(
        getattr(metadata, "name", None)
        or getattr(tool, "name", None)
        or getattr(tool, "__name__", None)
        or "tool"
    )


def _tool_schema(metadata: Any) -> dict[str, Any]:
    get_parameters = getattr(metadata, "get_parameters_dict", None)
    if callable(get_parameters):
        try:
            data = get_parameters()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            pass
    fn_schema = getattr(metadata, "fn_schema", None)
    model_json_schema = getattr(fn_schema, "model_json_schema", None)
    if callable(model_json_schema):
        try:
            data = model_json_schema()
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def _required_args_from_schema(schema: dict[str, Any]) -> list[str]:
    required = schema.get("required")
    if isinstance(required, list):
        return [str(item) for item in required]
    properties = schema.get("properties")
    if isinstance(properties, dict):
        return [str(key) for key in properties]
    return []


def _blocked_tool_output(
    decision: GuardDecision,
    tool_name: str,
    raw_input: dict[str, Any],
) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return _make_tool_output(tool_name, raw_input, decision, "blocked")
    if decision.requires_user or decision.requires_remote:
        return _make_tool_output(tool_name, raw_input, decision, "pending")
    if decision.decision_type == DecisionType.DEGRADE:
        return _make_tool_output(tool_name, raw_input, decision, "degraded")
    return None


def _blocked_result_output(
    decision: GuardDecision,
    tool_name: str,
    raw_input: dict[str, Any],
) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return _make_tool_output(tool_name, raw_input, decision, "blocked")
    if decision.decision_type == DecisionType.SANITIZE:
        return _make_tool_output(tool_name, raw_input, decision, "sanitized")
    if decision.requires_user or decision.requires_remote:
        return _make_tool_output(tool_name, raw_input, decision, "pending")
    return None


def _make_tool_output(
    tool_name: str,
    raw_input: dict[str, Any],
    decision: GuardDecision,
    status: str,
) -> Any:
    raw_output = {
        "agentguard": status,
        "tool": tool_name,
        "reason": decision.reason,
        "decision": decision.decision_type.value,
    }
    content = str(raw_output)
    tool_output_cls = _get_llamaindex_tool_output_class()
    if tool_output_cls is not None:
        try:
            return tool_output_cls(
                content=content,
                tool_name=tool_name,
                raw_input=raw_input,
                raw_output=raw_output,
                is_error=True,
            )
        except Exception:
            pass
    return {
        "content": content,
        "tool_name": tool_name,
        "raw_input": raw_input,
        "raw_output": raw_output,
        "is_error": True,
    }


@functools.lru_cache(maxsize=1)
def _get_llamaindex_tool_output_class() -> Any:
    try:
        from llama_index.core.tools import ToolOutput  # type: ignore[import-not-found]
    except Exception:
        return None
    return ToolOutput


def _blocked_llm_value(decision: GuardDecision) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return {"agentguard": "blocked", "reason": decision.reason}
    if decision.decision_type == DecisionType.SANITIZE:
        return {"agentguard": "sanitized", "reason": decision.reason}
    if decision.requires_user or decision.requires_remote:
        return {
            "agentguard": "pending",
            "reason": decision.reason,
            "decision": decision.decision_type.value,
        }
    if decision.decision_type == DecisionType.DEGRADE:
        return {
            "agentguard": "degraded",
            "reason": decision.reason,
            "decision": decision.decision_type.value,
        }
    return None


def _llamaindex_meta(*, label: str | None = None, owner: Any = None) -> dict[str, Any]:
    meta: dict[str, Any] = {"adapter": "llamaindex"}
    if label:
        meta["label"] = label
    if owner is not None:
        meta["owner_type"] = type(owner).__name__
        meta["owner_module"] = type(owner).__module__
    return meta


def _normalize_llamaindex_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(key): _normalize_llamaindex_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_llamaindex_value(item) for item in value]

    message = _normalize_message_like(value)
    if message is not None:
        return message

    tool_output = _normalize_tool_output_like(value)
    if tool_output is not None:
        return tool_output

    response = _normalize_response_like(value)
    if response is not None:
        return response

    for attr in ("model_dump", "to_dict", "dict"):
        dumper = getattr(value, attr, None)
        if callable(dumper):
            try:
                return _normalize_llamaindex_value(dumper())
            except Exception:
                continue
    return str(value)


def _normalize_llamaindex_llm_output(value: Any) -> Any:
    normalized = _normalize_llamaindex_value(value)
    llm_output = _extract_llamaindex_llm_output_fields(normalized)
    return llm_output if llm_output is not None else normalized


def _extract_llamaindex_llm_output_fields(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    candidate = value
    message = value.get("message")
    if isinstance(message, dict):
        candidate = message

    visible = _first_text_field(candidate, "content", "text", "delta")
    if visible is None:
        visible = _first_text_field(value, "content", "text", "output", "delta")
    if visible is None:
        return None

    thought = _extract_llamaindex_thought(candidate)
    final_output = visible
    if thought is None:
        parsed = _parse_tagged_llamaindex_output(visible)
        thought = parsed.thought
        final_output = parsed.final_output

    payload: dict[str, Any] = {"output": visible, "final_output": final_output}
    if thought is not None:
        payload["thought"] = thought
    return payload


def _first_text_field(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def _extract_llamaindex_thought(value: dict[str, Any]) -> str | None:
    additional_kwargs = value.get("additional_kwargs")
    if isinstance(additional_kwargs, dict):
        for key in ("reasoning_content", "reasoningContent", "thinking", "thought"):
            item = additional_kwargs.get(key)
            if isinstance(item, str) and item:
                return item

    blocks = value.get("blocks")
    if not isinstance(blocks, list):
        return None

    thought_parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").lower()
        if block_type not in {"thinkingblock", "thinking", "reasoningblock", "reasoning"}:
            continue
        text = _first_text_field(block, "content", "text", "thinking", "reasoning")
        if text:
            thought_parts.append(text)
    return "\n\n".join(thought_parts) or None


@dataclass(frozen=True)
class _ParsedLlamaIndexOutput:
    thought: str | None
    final_output: str


_LLAMAINDEX_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_LLAMAINDEX_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_tagged_llamaindex_output(output: str) -> _ParsedLlamaIndexOutput:
    thought_matches = list(_LLAMAINDEX_THOUGHT_TAG_RE.finditer(output))
    if not thought_matches:
        return _ParsedLlamaIndexOutput(thought=None, final_output=output)

    thought_parts = [match.group("body").strip() for match in thought_matches]
    thought = "\n\n".join(part for part in thought_parts if part) or None
    remainder = _LLAMAINDEX_THOUGHT_TAG_RE.sub("", output).strip()

    final_matches = list(_LLAMAINDEX_FINAL_TAG_RE.finditer(remainder))
    if final_matches:
        final_parts = [match.group("body").strip() for match in final_matches]
        final_output = "\n\n".join(part for part in final_parts if part)
    else:
        final_output = remainder

    return _ParsedLlamaIndexOutput(thought=thought, final_output=final_output)


def _normalize_message_like(value: Any) -> dict[str, Any] | None:
    if not any(hasattr(value, attr) for attr in ("role", "content", "blocks")):
        return None
    out: dict[str, Any] = {"type": type(value).__name__}
    for attr in ("role", "content", "blocks", "additional_kwargs"):
        attr_value = getattr(value, attr, None)
        if attr_value not in (None, [], {}, ""):
            out[attr] = _normalize_llamaindex_value(attr_value)
    return out


def _normalize_response_like(value: Any) -> dict[str, Any] | None:
    if not any(hasattr(value, attr) for attr in ("message", "text", "delta", "raw")):
        return None
    out: dict[str, Any] = {"type": type(value).__name__}
    for attr in ("message", "text", "delta", "raw", "additional_kwargs"):
        attr_value = getattr(value, attr, None)
        if attr_value not in (None, [], {}, ""):
            out[attr] = _normalize_llamaindex_value(attr_value)
    return out


def _normalize_tool_output_like(value: Any) -> dict[str, Any] | None:
    if not all(hasattr(value, attr) for attr in ("tool_name", "raw_input", "raw_output")):
        return None
    out: dict[str, Any] = {"type": type(value).__name__}
    for attr in ("content", "tool_name", "raw_input", "raw_output", "is_error"):
        if hasattr(value, attr):
            out[attr] = _normalize_llamaindex_value(getattr(value, attr))
    return out
