"""Shared normalization helpers for attach-mode agent adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, Protocol, runtime_checkable

from agentguard.tools.metadata import ToolMetadata


@dataclass(slots=True)
class LLMInputNormalization:
    payload: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMOutputNormalization:
    payload: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMInputDenormalization:
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolInvokeNormalization:
    arguments: dict[str, Any]
    capabilities: list[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResultNormalization:
    result: Any
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentEventNormalizer(Protocol):
    def normalize_llm_input(
        self,
        *,
        label: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> LLMInputNormalization: ...

    def normalize_llm_output(
        self,
        *,
        label: str,
        output: Any,
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> LLMOutputNormalization: ...

    def denormalize_llm_input(
        self,
        *,
        label: str,
        payload: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> LLMInputDenormalization: ...

    def normalize_tool_invoke(
        self,
        *,
        tool_metadata: ToolMetadata,
        arguments: dict[str, Any],
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> ToolInvokeNormalization: ...

    def normalize_tool_result(
        self,
        *,
        tool_name: str,
        result: Any = None,
        error: str | None = None,
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> ToolResultNormalization: ...


class _FallbackAgentEventNormalizer:
    """Fallback normalizer used when no adapter instance is available."""

    adapter_name = "base"

    def normalize_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, bytes):
            try:
                return value.decode("utf-8")
            except UnicodeDecodeError:
                return value.decode("utf-8", errors="replace")
        if isinstance(value, dict):
            return {str(key): self.normalize_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [self.normalize_value(item) for item in value]

        for attr in ("model_dump", "to_dict", "dict"):
            dumper = getattr(value, attr, None)
            if callable(dumper):
                try:
                    return self.normalize_value(dumper())
                except Exception:
                    continue

        content = getattr(value, "content", None)
        role = getattr(value, "role", None)
        if content is not None or role is not None:
            out: dict[str, Any] = {}
            if role is not None:
                out["role"] = self.normalize_value(role)
            if content is not None:
                out["content"] = self.normalize_value(content)
            return out

        return str(value)

    def _metadata(
        self,
        *,
        label: str | None = None,
        owner: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        if getattr(self, "adapter_name", None):
            meta["adapter"] = str(self.adapter_name)
        if label:
            meta["label"] = str(label)
        if owner is not None:
            meta["owner_type"] = type(owner).__name__
            meta["owner_module"] = type(owner).__module__
        if extra:
            meta.update(extra)
        return meta

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
        return LLMInputNormalization(
            payload={
                "label": label,
                "args": self.normalize_value(list(args)),
                "kwargs": self.normalize_value(dict(kwargs)),
            },
            metadata=self._metadata(label=label, owner=owner),
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
            payload=self.normalize_value(output),
            metadata=self._metadata(label=label, owner=owner),
        )

    def denormalize_llm_input(
        self,
        *,
        label: str,
        payload: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        fn: Callable[..., Any] | None = None,
        owner: Any = None,
    ) -> LLMInputDenormalization:
        denormalized = denormalize_llm_input_payload(
            payload=payload,
            args=args,
            kwargs=kwargs,
            fn=fn,
        )
        return LLMInputDenormalization(
            args=denormalized.args,
            kwargs=denormalized.kwargs,
            metadata=self._metadata(label=label, owner=owner),
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
        return ToolInvokeNormalization(
            arguments=self.normalize_value(arguments),
            capabilities=list(tool_metadata.capabilities),
            metadata=self._metadata(owner=owner),
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
            result=self.normalize_value(result),
            error=error,
            metadata=self._metadata(owner=owner),
        )


DEFAULT_AGENT_EVENT_NORMALIZER = _FallbackAgentEventNormalizer()


def denormalize_llm_input_payload(
    *,
    payload: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    fn: Callable[..., Any] | None = None,
) -> LLMInputDenormalization:
    current_args = list(args)
    current_kwargs = dict(kwargs)

    if isinstance(payload, dict):
        if "input" in payload:
            current_args, current_kwargs = _replace_primary_llm_input(
                payload["input"],
                args=current_args,
                kwargs=current_kwargs,
                fn=fn,
                preferred_keys=("input", "messages", "msg"),
            )
            extra_args = payload.get("args")
            if isinstance(extra_args, (list, tuple)):
                current_args = current_args[:1] + list(extra_args)
            elif extra_args is not None and len(current_args) <= 1:
                current_args = current_args[:1] + [extra_args]
            _merge_llm_payload_kwargs(
                current_kwargs,
                payload,
                skip_keys={"label", "input", "args"},
            )
            return LLMInputDenormalization(args=tuple(current_args), kwargs=current_kwargs)

        if "messages" in payload or "msg" in payload:
            primary_key = "messages" if "messages" in payload else "msg"
            current_args, current_kwargs = _replace_primary_llm_input(
                payload[primary_key],
                args=current_args,
                kwargs=current_kwargs,
                fn=fn,
                preferred_keys=(primary_key, "input"),
            )
            _merge_llm_payload_kwargs(
                current_kwargs,
                payload,
                skip_keys={"label", primary_key},
            )
            return LLMInputDenormalization(args=tuple(current_args), kwargs=current_kwargs)

        if "args" in payload or "kwargs" in payload:
            raw_args = payload.get("args", current_args)
            raw_kwargs = payload.get("kwargs", current_kwargs)
            next_args = tuple(raw_args) if isinstance(raw_args, (list, tuple)) else (raw_args,)
            next_kwargs = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else dict(current_kwargs)
            return LLMInputDenormalization(args=next_args, kwargs=next_kwargs)

    if not current_args and not current_kwargs:
        return LLMInputDenormalization(args=(payload,), kwargs={})

    current_args, current_kwargs = _replace_primary_llm_input(
        payload,
        args=current_args,
        kwargs=current_kwargs,
        fn=fn,
        preferred_keys=("input", "messages", "msg"),
    )
    return LLMInputDenormalization(args=tuple(current_args), kwargs=current_kwargs)


def _replace_primary_llm_input(
    value: Any,
    *,
    args: list[Any],
    kwargs: dict[str, Any],
    fn: Callable[..., Any] | None = None,
    preferred_keys: tuple[str, ...],
) -> tuple[list[Any], dict[str, Any]]:
    for key in preferred_keys:
        if key in kwargs:
            kwargs[key] = value
            return args, kwargs

    if args:
        args[0] = value
        return args, kwargs

    for key in preferred_keys:
        if _function_accepts_keyword(fn, key):
            kwargs[key] = value
            return args, kwargs

    kwargs[preferred_keys[0] if preferred_keys else "input"] = value
    return args, kwargs


def _merge_llm_payload_kwargs(
    kwargs: dict[str, Any],
    payload: dict[str, Any],
    *,
    skip_keys: set[str],
) -> None:
    for key, value in payload.items():
        if key in skip_keys:
            continue
        if key == "kwargs" and isinstance(value, dict):
            kwargs.update(value)
            continue
        kwargs[key] = value


def _function_accepts_keyword(fn: Callable[..., Any] | None, key: str) -> bool:
    if not callable(fn):
        return False
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    param = sig.parameters.get(key)
    if param is None:
        return False
    return param.kind in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }


__all__ = [
    "AgentEventNormalizer",
    "DEFAULT_AGENT_EVENT_NORMALIZER",
    "LLMInputDenormalization",
    "LLMInputNormalization",
    "LLMOutputNormalization",
    "ToolInvokeNormalization",
    "ToolResultNormalization",
    "denormalize_llm_input_payload",
]
