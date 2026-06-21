"""Shared normalization helpers for attach-mode agent adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
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


__all__ = [
    "AgentEventNormalizer",
    "DEFAULT_AGENT_EVENT_NORMALIZER",
    "LLMInputNormalization",
    "LLMOutputNormalization",
    "ToolInvokeNormalization",
    "ToolResultNormalization",
]
