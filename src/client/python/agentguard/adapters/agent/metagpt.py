"""MetaGPT agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

from collections.abc import Iterable
import contextvars
from dataclasses import dataclass
import functools
import inspect
import re
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter, LLMBinding, ToolBinding
from agentguard.adapters.agent.normalization import (
    LLMInputNormalization,
    LLMOutputNormalization,
    ToolInvokeNormalization,
    ToolResultNormalization,
)
from agentguard.adapters.agent.patching import (
    bind_arguments,
    guard_tool_after,
    guard_tool_before,
    is_guarded,
    mark_guarded,
    register_tool_metadata,
    set_attr,
    tool_name,
)
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.context import RuntimeContext
from agentguard.tools.metadata import ToolMetadata
from agentguard.utils.errors import AdapterError

_MAX_DISCOVERY_DEPTH = 4
_ACTION_RUN_ATTRS = ("run",)
_LLM_METHODS = ("aask",)
_CURRENT_METAGPT_CALL: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "agentguard_metagpt_call",
    default=None,
)


class MetaGPTAgentAdapter(BaseAgentAdapter):
    name = "metagpt"

    def can_wrap(self, agent: Any) -> bool:
        return any("metagpt" in _module_name(obj) for obj in _iter_candidate_objects(agent))

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        _ = context
        llm = _first_llm_object(agent)
        if llm is None:
            raise AdapterError("metagpt agent exposes no llm.aask")
        prompt = messages[-1].get("content", "") if messages else ""
        return llm.aask(prompt)

    def getllm(self, agent: Any) -> list[LLMBinding]:
        bindings: list[LLMBinding] = []
        seen: set[int] = set()
        for obj in _iter_candidate_objects(agent):
            for llm in _extract_llm_objects(obj):
                if llm is None or id(llm) in seen:
                    continue
                seen.add(id(llm))
                bindings.extend(
                    self.collect_llm_methods(llm, methods=_LLM_METHODS)
                )
        return bindings

    def gettools(self, agent: Any) -> list[ToolBinding]:
        bindings: list[ToolBinding] = []
        for obj in _iter_candidate_objects(agent):
            bindings.extend(_collect_tool_execution_map(obj, self))
            bindings.extend(_collect_action_run_tools(obj, self, root=agent))
        return bindings

    def normalize_llm_input(
        self,
        *,
        label: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        fn: Any = None,
        owner: Any = None,
    ) -> LLMInputNormalization:
        bound = bind_arguments(fn, args, kwargs) if callable(fn) else {}
        if not bound:
            bound = dict(kwargs)
            if args:
                bound["messages"] = args[0] if len(args) == 1 else list(args)

        payload: dict[str, Any] = {"label": label}
        first_message = bound.pop("msg", None)
        if first_message is None:
            first_message = bound.pop("messages", None)
        if first_message is not None:
            payload["messages"] = self.normalize_value(first_message)

        for key in ("system_msgs", "format_msgs", "images"):
            if key in bound:
                payload[key] = self.normalize_value(bound.pop(key))
        extra_kwargs = bound.pop("kwargs", None)
        if isinstance(extra_kwargs, dict):
            bound.update(extra_kwargs)
        if bound:
            payload["kwargs"] = self.normalize_value(bound)
        metadata = self._metadata(
            label=label,
            owner=owner,
            extra={"request": payload, **_metagpt_llm_extra(owner)},
        )
        return LLMInputNormalization(
            payload=payload.get("messages", payload),
            metadata=metadata,
        )

    def normalize_llm_output(
        self,
        *,
        label: str,
        output: Any,
        fn: Any = None,
        owner: Any = None,
    ) -> LLMOutputNormalization:
        _ = (label, fn)
        return LLMOutputNormalization(
            payload=_normalize_metagpt_llm_output(self.normalize_value(output), owner=owner),
            metadata=self._metadata(
                label=label,
                owner=owner,
                extra=_metagpt_llm_extra(owner),
            ),
        )

    def normalize_tool_invoke(
        self,
        *,
        tool_metadata: ToolMetadata,
        arguments: dict[str, Any],
        fn: Any = None,
        owner: Any = None,
    ) -> ToolInvokeNormalization:
        _ = fn
        return ToolInvokeNormalization(
            arguments=self.normalize_value(arguments),
            capabilities=list(tool_metadata.capabilities),
            metadata=self._metadata(
                owner=owner,
                extra=_metagpt_tool_extra(tool_metadata.name, owner=owner),
            ),
        )

    def normalize_tool_result(
        self,
        *,
        tool_name: str,
        result: Any = None,
        error: str | None = None,
        fn: Any = None,
        owner: Any = None,
    ) -> ToolResultNormalization:
        _ = fn
        return ToolResultNormalization(
            result=self.normalize_value(result),
            error=error,
            metadata=self._metadata(
                owner=owner,
                extra=_metagpt_tool_extra(tool_name, owner=owner),
            ),
        )


def _module_name(obj: Any) -> str:
    return type(obj).__module__ or ""


def _first_llm_object(agent: Any) -> Any | None:
    for obj in _iter_candidate_objects(agent):
        for llm in _extract_llm_objects(obj):
            if callable(getattr(llm, "aask", None)):
                return llm
    return None


def _iter_candidate_objects(root: Any) -> list[Any]:
    objects: list[Any] = []
    seen: set[int] = set()

    def visit(obj: Any, depth: int) -> None:
        if obj is None or depth > _MAX_DISCOVERY_DEPTH:
            return
        ident = id(obj)
        if ident in seen:
            return
        seen.add(ident)
        objects.append(obj)

        if isinstance(obj, dict):
            for value in list(obj.values()):
                visit(value, depth + 1)
            return
        if isinstance(obj, (list, tuple, set, frozenset)):
            for value in list(obj):
                visit(value, depth + 1)
            return
        if _is_primitive(obj):
            return

        for attr in _candidate_attrs(obj):
            try:
                value = getattr(obj, attr)
            except Exception:
                continue
            if callable(value) and attr not in {"llm", "context"}:
                continue
            visit(value, depth + 1)

    visit(root, 0)
    return objects


def _candidate_attrs(obj: Any) -> tuple[str, ...]:
    names = [
        "llm",
        "private_llm",
        "context",
        "private_context",
        "rc",
        "todo",
        "actions",
        "_actions",
        "execute_code",
        "tool_execution_map",
        "planner",
        "plan",
        "browser",
        "editor",
    ]
    extra = getattr(obj, "__dict__", None)
    if isinstance(extra, dict):
        for name in extra:
            if name.startswith("__"):
                continue
            if name not in names:
                names.append(name)
    return tuple(names)


def _is_primitive(obj: Any) -> bool:
    return isinstance(obj, (str, bytes, bool, int, float, complex))


def _extract_llm_objects(obj: Any) -> Iterable[Any]:
    for attr in ("llm", "private_llm"):
        try:
            llm = getattr(obj, attr)
        except Exception:
            continue
        if callable(getattr(llm, "aask", None)):
            yield llm

    context = None
    for attr in ("context", "private_context"):
        try:
            context = getattr(obj, attr)
        except Exception:
            continue
        if context is not None:
            for name in ("_llm", "llm", "private_llm"):
                try:
                    llm = getattr(context, name)
                except Exception:
                    continue
                if callable(getattr(llm, "aask", None)):
                    yield llm


def _collect_tool_execution_map(obj: Any, adapter: MetaGPTAgentAdapter) -> list[ToolBinding]:
    registry = getattr(obj, "tool_execution_map", None)
    if not isinstance(registry, dict):
        return []

    bindings: list[ToolBinding] = []
    for name, fn in list(registry.items()):
        if not callable(fn) or is_guarded(fn):
            continue
        bindings.append(
            adapter.build_tool_binding(
                name=str(name),
                fn=fn,
                container=registry,
                key=name,
                tool=fn,
                installer=_install_metagpt_tool_binding,
                metadata={"logical_id": ("tool_execution_map", str(name))},
            )
        )
    return bindings


def _collect_action_run_tools(obj: Any, adapter: MetaGPTAgentAdapter, *, root: Any = None) -> list[ToolBinding]:
    if _is_llm_like(obj):
        return []
    if isinstance(obj, (dict, list, tuple, set, frozenset)) or _is_primitive(obj):
        return []
    if not _is_metagpt_action_like(obj):
        return []

    bindings: list[ToolBinding] = []
    for attr in _ACTION_RUN_ATTRS:
        fn = getattr(obj, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        name = _action_tool_name(obj, fn)
        if name in {"RoleZero", "DataInterpreter"}:
            continue
        bindings.append(
            adapter.build_tool_binding(
                name=name,
                fn=fn,
                owner=obj,
                attr=attr,
                tool=obj,
                installer=_install_metagpt_tool_binding,
                metadata={
                    "logical_id": ("action_run", id(obj), attr),
                    "metagpt_role": _find_role_for_action(root, obj) if root is not None else None,
                },
            )
        )
    return bindings


def _find_role_for_action(root: Any, action: Any) -> Any | None:
    for obj in _iter_candidate_objects(root):
        if obj is action or _is_primitive(obj):
            continue
        actions = getattr(obj, "actions", None)
        if isinstance(actions, list) and any(item is action for item in actions):
            return obj
        for attr in ("execute_code", "todo"):
            try:
                if getattr(obj, attr) is action:
                    return obj
            except Exception:
                continue
    return None


def _is_llm_like(obj: Any) -> bool:
    return callable(getattr(obj, "aask", None)) and (
        "llm" in type(obj).__name__.lower() or "provider" in _module_name(obj)
    )


def _is_metagpt_action_like(obj: Any) -> bool:
    module = _module_name(obj)
    if "metagpt.actions" in module:
        return True
    if "metagpt.roles" in module:
        return False
    return hasattr(obj, "config") and hasattr(obj, "llm") and hasattr(obj, "run")


def _action_tool_name(obj: Any, fn: Any) -> str:
    explicit = getattr(obj, "name", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    return tool_name(obj, fn, fallback=type(obj).__name__)


def _normalize_metagpt_llm_output(value: Any, *, owner: Any = None) -> dict[str, Any]:
    visible = _metagpt_visible_output_text(value)
    thought = _metagpt_owner_thought(owner) or _extract_metagpt_thought(value)
    final_output: Any = visible
    if isinstance(visible, str):
        parsed = _parse_tagged_metagpt_llm_output(visible)
        if thought is None:
            thought = parsed.thought
        if parsed.thought is not None:
            final_output = parsed.final_output

    payload: dict[str, Any] = {"output": visible, "final_output": final_output}
    if thought is not None:
        payload["thought"] = thought
    return payload


def _metagpt_visible_output_text(value: Any) -> Any:
    if isinstance(value, dict):
        text = _first_non_empty_text(value, "output", "content", "text", "message")
        if text is not None:
            return text
    return value


def _metagpt_owner_thought(owner: Any) -> str | None:
    thought = getattr(owner, "reasoning_content", None)
    if isinstance(thought, str) and thought:
        return thought
    return None


def _extract_metagpt_thought(value: Any) -> str | None:
    if isinstance(value, dict):
        direct = _first_non_empty_text(
            value,
            "thought",
            "reasoning_content",
            "reasoningContent",
            "thinking",
            "reasoning",
        )
        if direct is not None:
            return direct
        for container_key in ("additional_kwargs", "response_metadata", "metadata", "extra"):
            nested = value.get(container_key)
            if isinstance(nested, dict):
                nested_thought = _extract_metagpt_thought(nested)
                if nested_thought is not None:
                    return nested_thought
        for blocks_key in ("parts", "content", "output"):
            blocks = value.get(blocks_key)
            if isinstance(blocks, list):
                block_thought = _extract_metagpt_thought_from_blocks(blocks)
                if block_thought is not None:
                    return block_thought
        return None

    direct = _first_non_empty_attr(
        value,
        "thought",
        "reasoning_content",
        "reasoningContent",
        "thinking",
        "reasoning",
    )
    if direct is not None:
        return direct
    parts = getattr(value, "parts", None)
    if isinstance(parts, list):
        return _extract_metagpt_thought_from_blocks(parts)
    return None


def _extract_metagpt_thought_from_blocks(blocks: list[Any]) -> str | None:
    thought_parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            block_type = str(block.get("type") or block.get("kind") or "").lower()
            if block_type in {"thinking", "thinkingblock", "reasoning", "reasoningblock"}:
                text = _first_non_empty_text(block, "content", "text", "thinking", "reasoning", "summary")
                if text is not None:
                    thought_parts.append(text)
        else:
            block_type = str(getattr(block, "type", None) or getattr(block, "kind", None) or "").lower()
            if block_type in {"thinking", "thinkingblock", "reasoning", "reasoningblock"}:
                text = _first_non_empty_attr(block, "content", "text", "thinking", "reasoning", "summary")
                if text is not None:
                    thought_parts.append(text)
    return "\n\n".join(thought_parts) or None


def _first_non_empty_text(value: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def _first_non_empty_attr(value: Any, *keys: str) -> str | None:
    for key in keys:
        item = getattr(value, key, None)
        if isinstance(item, str) and item:
            return item
    return None


@dataclass(frozen=True)
class _ParsedMetaGPTLLMOutput:
    thought: str | None
    final_output: str


_METAGPT_THOUGHT_TAG_RE = re.compile(
    r"<(?P<tag>think|thought|reason|reasoning)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)
_METAGPT_FINAL_TAG_RE = re.compile(
    r"<(?P<tag>answer|final|final_output)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    flags=re.IGNORECASE | re.DOTALL,
)


def _parse_tagged_metagpt_llm_output(output: str) -> _ParsedMetaGPTLLMOutput:
    thought_matches = list(_METAGPT_THOUGHT_TAG_RE.finditer(output))
    if not thought_matches:
        return _ParsedMetaGPTLLMOutput(thought=None, final_output=output)

    thought_parts = [match.group("body").strip() for match in thought_matches]
    thought = "\n\n".join(part for part in thought_parts if part) or None
    remainder = _METAGPT_THOUGHT_TAG_RE.sub("", output).strip()

    final_matches = list(_METAGPT_FINAL_TAG_RE.finditer(remainder))
    if final_matches:
        final_parts = [match.group("body").strip() for match in final_matches]
        final_output = "\n\n".join(part for part in final_parts if part)
    else:
        final_output = remainder

    return _ParsedMetaGPTLLMOutput(thought=thought, final_output=final_output)


def _metagpt_llm_extra(owner: Any = None) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if owner is not None:
        extra["llm_id"] = f"{type(owner).__module__}.{type(owner).__name__}:{id(owner)}"
        config = getattr(owner, "config", None)
        for attr, key in (
            ("model", "llm_model"),
            ("api_type", "llm_api_type"),
            ("base_url", "llm_base_url"),
            ("llm_type", "llm_type"),
        ):
            value = getattr(owner, attr, None)
            if value is None and config is not None:
                value = getattr(config, attr, None)
            if value is not None:
                extra[key] = str(value)
        config_name = getattr(config, "name", None) if config is not None else None
        if config_name:
            extra["llm_config_name"] = str(config_name)

    current = _CURRENT_METAGPT_CALL.get()
    if current:
        extra.update({key: value for key, value in current.items() if value is not None})
    return extra


def _metagpt_action_context(action: Any, role: Any = None) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    action_name = getattr(action, "name", None)
    if action_name:
        extra["metagpt_action_name"] = str(action_name)
    extra["metagpt_action_type"] = type(action).__name__
    extra["metagpt_action_module"] = type(action).__module__
    if role is not None:
        role_name = getattr(role, "name", None)
        role_profile = getattr(role, "profile", None)
        role_goal = getattr(role, "goal", None)
        if role_name:
            extra["metagpt_role_name"] = str(role_name)
        if role_profile:
            extra["metagpt_role_profile"] = str(role_profile)
        if role_goal:
            extra["metagpt_role_goal"] = str(role_goal)
        extra["metagpt_role_type"] = type(role).__name__
        extra["metagpt_role_module"] = type(role).__module__
    return extra


def _metagpt_tool_extra(tool_name: str, *, owner: Any = None) -> dict[str, Any]:
    extra = {"metagpt_tool_name": tool_name}
    if isinstance(tool_name, str) and "." in tool_name:
        command_owner, command_name = tool_name.split(".", 1)
        extra["command_owner"] = command_owner
        extra["command_name"] = command_name
    if owner is not None:
        explicit_name = getattr(owner, "name", None)
        if isinstance(explicit_name, str) and explicit_name:
            extra["action_name"] = explicit_name
        extra.update(_metagpt_action_context(owner))
    current = _CURRENT_METAGPT_CALL.get()
    if current:
        extra.update({key: value for key, value in current.items() if value is not None})
    return extra


def _install_metagpt_tool_binding(
    guard: Any,
    binding: ToolBinding,
    adapter: MetaGPTAgentAdapter,
) -> int:
    fn = binding.callable
    if not callable(fn) or is_guarded(fn):
        return 0

    owner = binding.tool or binding.owner or fn
    call_context = _metagpt_action_context(
        owner,
        role=binding.metadata.get("metagpt_role"),
    )
    meta_kwargs: dict[str, Any] = {}
    schema = getattr(owner, "schemas", None)
    if isinstance(schema, dict):
        meta_kwargs["schema"] = schema
    tags = getattr(owner, "tags", None)
    if isinstance(tags, list):
        meta_kwargs["capabilities"] = [str(tag) for tag in tags]
    metadata = register_tool_metadata(
        guard,
        fn,
        name=binding.name,
        tool=owner,
        capabilities=list(binding.capabilities or meta_kwargs.pop("capabilities", [])),
        **meta_kwargs,
    )

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            token = _CURRENT_METAGPT_CALL.set(call_context)
            try:
                arguments = _build_metagpt_tool_arguments(fn, args, kwargs)
                decision = guard_tool_before(
                    guard,
                    metadata,
                    arguments,
                    normalizer=adapter,
                    fn=fn,
                    owner=owner,
                )
                blocked = _blocked_metagpt_tool_value(decision, metadata.name)
                if blocked is not None:
                    return blocked
                try:
                    value = await fn(*args, **kwargs)
                except Exception as exc:
                    guard_tool_after(
                        guard,
                        metadata.name,
                        error=str(exc),
                        normalizer=adapter,
                        fn=fn,
                        owner=owner,
                    )
                    raise
                result_decision = guard_tool_after(
                    guard,
                    metadata.name,
                    value,
                    normalizer=adapter,
                    fn=fn,
                    owner=owner,
                )
                result_blocked = _blocked_metagpt_result_value(result_decision, metadata.name)
                return result_blocked if result_blocked is not None else value
            finally:
                _CURRENT_METAGPT_CALL.reset(token)

        return _install_metagpt_wrapped_binding(binding, mark_guarded(async_wrapper))

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _CURRENT_METAGPT_CALL.set(call_context)
        try:
            arguments = _build_metagpt_tool_arguments(fn, args, kwargs)
            decision = guard_tool_before(
                guard,
                metadata,
                arguments,
                normalizer=adapter,
                fn=fn,
                owner=owner,
            )
            blocked = _blocked_metagpt_tool_value(decision, metadata.name)
            if blocked is not None:
                return blocked
            try:
                value = fn(*args, **kwargs)
            except Exception as exc:
                guard_tool_after(
                    guard,
                    metadata.name,
                    error=str(exc),
                    normalizer=adapter,
                    fn=fn,
                    owner=owner,
                )
                raise
            result_decision = guard_tool_after(
                guard,
                metadata.name,
                value,
                normalizer=adapter,
                fn=fn,
                owner=owner,
            )
            result_blocked = _blocked_metagpt_result_value(result_decision, metadata.name)
            return result_blocked if result_blocked is not None else value
        finally:
            _CURRENT_METAGPT_CALL.reset(token)

    return _install_metagpt_wrapped_binding(binding, mark_guarded(wrapper))


def _install_metagpt_wrapped_binding(binding: ToolBinding, wrapped: Any) -> int:
    if binding.owner is not None and binding.attr:
        return 1 if set_attr(binding.owner, binding.attr, wrapped) else 0
    if binding.container is not None:
        try:
            binding.container[binding.key] = wrapped
            return 1
        except Exception:
            return 0
    return 0


def _build_metagpt_tool_arguments(
    fn: Any,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    return bind_arguments(fn, args, kwargs)


def _blocked_metagpt_tool_value(decision: GuardDecision, tool: str) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return {"agentguard": "blocked", "tool": tool, "reason": decision.reason}
    if decision.requires_user or decision.requires_remote:
        return {
            "agentguard": "pending",
            "tool": tool,
            "reason": decision.reason,
            "decision": decision.decision_type.value,
        }
    if decision.decision_type == DecisionType.DEGRADE:
        return {"agentguard": "degraded", "tool": tool, "reason": decision.reason}
    return None


def _blocked_metagpt_result_value(decision: GuardDecision, tool: str) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return {"agentguard": "blocked", "tool": tool, "reason": decision.reason}
    if decision.decision_type == DecisionType.SANITIZE:
        return {"agentguard": "sanitized", "tool": tool, "reason": decision.reason}
    if decision.requires_user or decision.requires_remote:
        return {
            "agentguard": "pending",
            "tool": tool,
            "reason": decision.reason,
            "decision": decision.decision_type.value,
        }
    return None


__all__ = ["MetaGPTAgentAdapter"]
