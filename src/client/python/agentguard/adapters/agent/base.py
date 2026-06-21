"""Agent adapter interface for attach-mode integrations."""
from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from typing import Any, Callable

from agentguard.adapters.agent.normalization import (
    LLMInputNormalization,
    LLMOutputNormalization,
    ToolInvokeNormalization,
    ToolResultNormalization,
)
from agentguard.schemas.context import RuntimeContext
from agentguard.tools.metadata import ToolMetadata
from agentguard.utils.errors import AdapterError


@dataclass(slots=True)
class ToolBinding:
    name: str
    parameters: dict[str, Any]
    callable: Callable[..., Any]
    owner: Any = None
    attr: str | None = None
    tool: Any = None
    capabilities: list[str] | None = None
    container: Any = None
    key: Any = None
    installer: Callable[[Any, "ToolBinding", "BaseAgentAdapter"], int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.callable(*args, **kwargs)


@dataclass(slots=True)
class LLMBinding:
    label: str
    callable: Callable[..., Any]
    owner: Any = None
    attr: str | None = None
    container: Any = None
    key: Any = None
    installer: Callable[[Any, "LLMBinding", "BaseAgentAdapter"], int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.callable(*args, **kwargs)


class BaseAgentAdapter:
    name: str = "base"

    def __init__(self) -> None:
        self.toolslist: list[ToolBinding] = []
        self.llms: list[LLMBinding] = []

    @property
    def adapter_name(self) -> str:
        return str(self.name)

    def gettools(self, agent: Any) -> list[ToolBinding]:
        """Return framework tool bindings; concrete adapters must implement this."""
        _ = agent
        raise NotImplementedError

    def getllm(self, agent: Any) -> list[LLMBinding]:
        """Return framework LLM call bindings; concrete adapters must implement this."""
        _ = agent
        raise NotImplementedError
    
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
        meta: dict[str, Any] = {"adapter": self.adapter_name}
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

    def can_wrap(self, agent: Any) -> bool:
        raise NotImplementedError

    def attach(
        self,
        agent: Any,
        guard: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch a framework object in-place while preserving its native loop."""
        patched = {"tools": 0, "llm": 0}
        if wrap_tools:
            patched["tools"] += self.patchtool(agent, guard)
        if wrap_llm:
            patched["llm"] += self.patchLLM(agent, guard)
        return patched

    def patchtool(self, agent: Any, guard: Any) -> int:
        self.toolslist = self._dedupe_bindings(list(self.gettools(agent) or []))
        patched = 0
        counted: set[tuple[Any, ...]] = set()
        for binding in self.toolslist:
            if not self._patch_tool_binding(binding, guard):
                continue
            logical_key = self._tool_binding_key(binding)
            if logical_key in counted:
                continue
            counted.add(logical_key)
            patched += 1
        return patched

    def patchLLM(self, agent: Any, guard: Any) -> int:
        self.llms = self._dedupe_bindings(list(self.getllm(agent) or []))
        patched = 0
        for binding in self.llms:
            patched += self._patch_llm_binding(binding, guard)
        return patched

    def _dedupe_bindings(self, bindings: list[ToolBinding] | list[LLMBinding]) -> list[Any]:
        unique: list[Any] = []
        seen: set[tuple[Any, ...]] = set()
        for binding in bindings:
            key = self._binding_install_key(binding)
            if key in seen:
                continue
            seen.add(key)
            unique.append(binding)
        return unique

    def _binding_install_key(self, binding: ToolBinding | LLMBinding) -> tuple[Any, ...]:
        owner = getattr(binding, "owner", None)
        attr = getattr(binding, "attr", None)
        if owner is not None and attr:
            return ("owner", id(owner), attr)

        container = getattr(binding, "container", None)
        key = getattr(binding, "key", None)
        if container is not None:
            return ("container", id(container), self._hashable_binding_value(key))

        return ("callable", id(getattr(binding, "callable", None)))

    def _tool_binding_key(self, binding: ToolBinding) -> tuple[Any, ...]:
        logical_id = binding.metadata.get("logical_id")
        if logical_id is not None:
            return ("logical", self._hashable_binding_value(logical_id))

        if binding.tool is not None:
            return ("tool", id(binding.tool))

        if binding.owner is not None:
            return ("owner", id(binding.owner))

        if binding.container is not None:
            return ("container", id(binding.container), self._hashable_binding_value(binding.key))

        return ("callable", id(binding.callable))

    def _hashable_binding_value(self, value: Any) -> Any:
        try:
            hash(value)
            return value
        except Exception:
            return repr(value)



    def run(self, agent: Any, input_data: Any, context: RuntimeContext) -> Any:
        """Raw, unguarded run of the underlying agent (best effort)."""
        _ = context
        if callable(agent):
            return agent(input_data)
        raise AdapterError(f"{self.name}: agent is not runnable")

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        """Produce one LLM turn given the running message list."""
        raise NotImplementedError

    def describe_parameters(self, fn: Callable[..., Any]) -> dict[str, Any]:
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return {}

        params: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            entry = {
                "kind": param.kind.name.lower(),
                "required": param.default is inspect.Signature.empty,
            }
            if param.default is not inspect.Signature.empty:
                entry["default"] = self.normalize_value(param.default)
            if param.annotation is not inspect.Signature.empty:
                ann = param.annotation
                entry["annotation"] = getattr(ann, "__name__", str(ann))
            params[name] = entry
        return params

    def extract_tool_callable(
        self,
        tool: Any,
        *,
        attrs: tuple[str, ...] = ("func", "_func"),
    ) -> tuple[Callable[..., Any] | None, str | None]:
        from agentguard.adapters.agent.patching import is_guarded

        for attr in attrs:
            fn = getattr(tool, attr, None)
            if callable(fn) and not is_guarded(fn):
                return fn, attr
        return None, None

    def resolve_attr_path(self, obj: Any, path: str) -> tuple[Any, str, Any]:
        if "." not in path:
            return obj, path, getattr(obj, path, None)

        parts = path.split(".")
        target = obj
        for part in parts[:-1]:
            target = getattr(target, part, None)
            if target is None:
                return obj, parts[-1], None
        leaf = parts[-1]
        return target, leaf, getattr(target, leaf, None)

    def build_tool_binding(
        self,
        *,
        name: str,
        fn: Callable[..., Any],
        owner: Any = None,
        attr: str | None = None,
        tool: Any = None,
        capabilities: list[str] | None = None,
        container: Any = None,
        key: Any = None,
        installer: Callable[[Any, ToolBinding, "BaseAgentAdapter"], int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ToolBinding:
        return ToolBinding(
            name=name,
            parameters=self.describe_parameters(fn),
            callable=fn,
            owner=owner,
            attr=attr,
            tool=tool,
            capabilities=list(capabilities) if capabilities is not None else None,
            container=container,
            key=key,
            installer=installer,
            metadata=dict(metadata or {}),
        )

    def build_llm_binding(
        self,
        *,
        label: str,
        fn: Callable[..., Any],
        owner: Any = None,
        attr: str | None = None,
        container: Any = None,
        key: Any = None,
        installer: Callable[[Any, LLMBinding, "BaseAgentAdapter"], int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMBinding:
        return LLMBinding(
            label=label,
            callable=fn,
            owner=owner,
            attr=attr,
            container=container,
            key=key,
            installer=installer,
            metadata=dict(metadata or {}),
        )

    def collect_tool_list(
        self,
        tools_list: list[Any],
        *,
        func_attrs: tuple[str, ...] = ("func", "_func"),
        run_json_attr: str = "run_json",
    ) -> list[ToolBinding]:
        from agentguard.adapters.agent.patching import is_guarded, tool_name

        bindings: list[ToolBinding] = []
        for idx, tool in enumerate(tools_list):
            if is_guarded(tool):
                continue

            fn, attr = self.extract_tool_callable(tool, attrs=func_attrs)
            if fn is not None and attr is not None:
                bindings.append(
                    self.build_tool_binding(
                        name=tool_name(tool, fn, fallback=f"tool_{idx}"),
                        fn=fn,
                        owner=tool,
                        attr=attr,
                        tool=tool,
                    )
                )
                continue

            run_json = getattr(tool, run_json_attr, None)
            if callable(run_json) and not is_guarded(run_json):
                bindings.append(
                    self.build_tool_binding(
                        name=tool_name(tool, run_json, fallback=f"tool_{idx}"),
                        fn=run_json,
                        owner=tool,
                        attr=run_json_attr,
                        tool=tool,
                    )
                )
                continue

            if callable(tool) and not is_guarded(tool):
                bindings.append(
                    self.build_tool_binding(
                        name=tool_name(tool, fallback=f"tool_{idx}"),
                        fn=tool,
                        container=tools_list,
                        key=idx,
                        tool=tool,
                    )
                )
        return bindings

    def collect_function_map(self, registry: dict[str, Any]) -> list[ToolBinding]:
        from agentguard.adapters.agent.patching import is_guarded

        bindings: list[ToolBinding] = []
        for name, fn in list(registry.items()):
            if not callable(fn) or is_guarded(fn):
                continue
            bindings.append(
                self.build_tool_binding(
                    name=str(name),
                    fn=fn,
                    container=registry,
                    key=name,
                    tool=fn,
                )
            )
        return bindings

    def collect_register_function(self, agent: Any) -> list[ToolBinding]:
        from agentguard.adapters.agent.patching import is_guarded

        original = getattr(agent, "register_function", None)
        if not callable(original) or is_guarded(original):
            return []
        return [
            self.build_tool_binding(
                name="register_function",
                fn=original,
                owner=agent,
                attr="register_function",
                tool=agent,
                installer=self._install_register_function_binding,
            )
        ]

    def collect_llm_methods(self, obj: Any, *, methods: tuple[str, ...]) -> list[LLMBinding]:
        from agentguard.adapters.agent.patching import is_guarded

        bindings: list[LLMBinding] = []
        for label in methods:
            target, attr, fn = self.resolve_attr_path(obj, label)
            if not callable(fn) or is_guarded(fn):
                continue
            bindings.append(
                self.build_llm_binding(
                    label=label,
                    fn=fn,
                    owner=target,
                    attr=attr,
                )
            )
        return bindings

    def _patch_tool_binding(self, binding: ToolBinding, guard: Any) -> int:
        from agentguard.adapters.agent.patching import is_guarded, make_guarded_tool

        if binding.installer is not None:
            return int(binding.installer(guard, binding, self) or 0)
        if not callable(binding.callable) or is_guarded(binding.callable):
            return 0
        wrapped = make_guarded_tool(
            guard,
            binding.callable,
            name=binding.name,
            tool=binding.tool or binding.owner or binding.callable,
            capabilities=list(binding.capabilities or []),
            normalizer=self,
            owner=binding.tool or binding.owner,
        )
        return self._install_bound_callable(binding, wrapped)

    def _patch_llm_binding(self, binding: LLMBinding, guard: Any) -> int:
        from agentguard.adapters.agent.patching import is_guarded, make_guarded_llm_callable

        if binding.installer is not None:
            return int(binding.installer(guard, binding, self) or 0)
        if not callable(binding.callable) or is_guarded(binding.callable):
            return 0
        wrapped = make_guarded_llm_callable(
            guard,
            binding.callable,
            label=binding.label,
            normalizer=self,
            owner=binding.owner,
        )
        return self._install_bound_callable(binding, wrapped)

    def _install_bound_callable(self, binding: ToolBinding | LLMBinding, wrapped: Any) -> int:
        from agentguard.adapters.agent.patching import mark_patched, set_attr

        owner = getattr(binding, "owner", None)
        attr = getattr(binding, "attr", None)
        if owner is not None and attr:
            if set_attr(owner, attr, wrapped):
                mark_patched(owner)
                return 1
            return 0

        container = getattr(binding, "container", None)
        key = getattr(binding, "key", None)
        if container is not None:
            try:
                container[key] = wrapped
                return 1
            except Exception:
                return 0
        return 0

    def _install_register_function_binding(
        self,
        guard: Any,
        binding: ToolBinding,
        adapter: "BaseAgentAdapter",
    ) -> int:
        from agentguard.adapters.agent.patching import is_guarded, make_guarded_tool, set_attr, tool_name

        original = binding.callable
        agent = binding.owner
        if not callable(original) or agent is None or is_guarded(original):
            return 0

        def patched(func: Any = None, /, **kwargs: Any) -> Any:
            if callable(func) and not is_guarded(func):
                name = kwargs.get("name") or tool_name(func)
                func = make_guarded_tool(
                    guard,
                    func,
                    name=name,
                    tool=func,
                    normalizer=adapter,
                    owner=func,
                )
            return original(func, **kwargs)

        return 1 if set_attr(agent, "register_function", patched) else 0


__all__ = ["BaseAgentAdapter", "LLMBinding", "ToolBinding"]
