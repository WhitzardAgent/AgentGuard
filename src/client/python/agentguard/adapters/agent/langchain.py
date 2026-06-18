"""LangChain agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Sequence
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent.patching import (
    bind_arguments,
    guard_tool_after,
    guard_tool_before,
    is_guarded,
    mark_guarded,
    make_guarded_tool,
    register_tool_metadata,
    set_attr,
    tool_name,
)
from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.utils.errors import AdapterError


def _module_name(obj: Any) -> str:
    return type(obj).__module__ or ""


class LangChainAgentAdapter(BaseAgentAdapter):
    name = "langchain"

    def can_wrap(self, agent: Any) -> bool:
        module_name = _module_name(agent)
        return "langchain" in module_name or "langgraph" in module_name

    def generate(self, agent: Any, messages: list[dict[str, Any]], context: RuntimeContext) -> Any:
        prompt = messages[-1].get("content", "") if messages else ""
        for method in ("invoke", "run", "predict"):
            fn = getattr(agent, method, None)
            if callable(fn):
                try:
                    return fn(prompt)
                except Exception as exc:
                    raise AdapterError(f"langchain agent invoke failed: {exc}") from exc
        raise AdapterError("langchain agent exposes no invoke/run/predict")

    def patchtool(self, agent: Any, guard: Any) -> int:
        patched = 0
        patched += _patch_container_tools(agent, guard)
        for _, tool_node in _iter_tool_nodes(agent):
            patched += _patch_tool_node(tool_node, guard)

        nodes = getattr(agent, "nodes", None) or getattr(agent, "_nodes", None)
        if isinstance(nodes, dict):
            iterable = nodes.values()
        elif isinstance(nodes, (list, tuple, set)):
            iterable = nodes
        else:
            iterable = []

        for node in iterable:
            patched += _patch_container_tools(node, guard)
            runnable = getattr(node, "runnable", None)
            if runnable is not None:
                patched += _patch_container_tools(runnable, guard)
        return patched

    def patchLLM(self, agent: Any, guard: Any) -> int:
        return _patch_langchain_llm(agent, guard)


def _iter_tool_nodes(agent: Any) -> list[tuple[str, Any]]:
    tool_nodes: list[tuple[str, Any]] = []
    seen: set[int] = set()

    compiled_nodes = getattr(agent, "nodes", None)
    if isinstance(compiled_nodes, dict):
        for name, node in compiled_nodes.items():
            tool_node = getattr(node, "bound", None)
            if not isinstance(getattr(tool_node, "tools_by_name", None), dict):
                continue
            ident = id(tool_node)
            if ident in seen:
                continue
            seen.add(ident)
            tool_nodes.append((str(name), tool_node))

    builder_nodes = getattr(getattr(agent, "builder", None), "nodes", None)
    if isinstance(builder_nodes, dict):
        for name, node in builder_nodes.items():
            tool_node = getattr(node, "data", None)
            if not isinstance(getattr(tool_node, "tools_by_name", None), dict):
                continue
            ident = id(tool_node)
            if ident in seen:
                continue
            seen.add(ident)
            tool_nodes.append((str(name), tool_node))

    return tool_nodes


def _patch_container_tools(container: Any, guard: Any) -> int:
    patched = 0
    for attr in ("tools_by_name", "_tools_by_name"):
        tools = getattr(container, attr, None)
        if isinstance(tools, dict):
            for name, tool in list(tools.items()):
                if callable(tool) and not hasattr(tool, "invoke"):
                    tools[name] = make_guarded_tool(guard, tool, name=str(name), tool=tool)
                    patched += 1
                else:
                    patched += _patch_tool_object(tool, guard, name=str(name))

    for attr in ("tools", "_tools"):
        tools = getattr(container, attr, None)
        if isinstance(tools, dict):
            for name, tool in list(tools.items()):
                if callable(tool) and not hasattr(tool, "invoke"):
                    tools[name] = make_guarded_tool(guard, tool, name=str(name), tool=tool)
                    patched += 1
                else:
                    patched += _patch_tool_object(tool, guard, name=str(name))
        elif isinstance(tools, list):
            for idx, tool in enumerate(list(tools)):
                if callable(tool) and not hasattr(tool, "invoke"):
                    name = tool_name(tool, fallback=f"tool_{idx}")
                    tools[idx] = make_guarded_tool(guard, tool, name=name, tool=tool)
                    patched += 1
                else:
                    patched += _patch_tool_object(
                        tool, guard, name=tool_name(tool, fallback=f"tool_{idx}")
                    )
    return patched


def _patch_tool_node(tool_node: Any, guard: Any) -> int:
    tools_by_name = getattr(tool_node, "tools_by_name", None)
    if not isinstance(tools_by_name, dict):
        return 0

    patched = 0
    for name, tool in list(tools_by_name.items()):
        patched += _patch_tool_object(tool, guard, name=str(name))
    return patched


def _patch_langchain_llm(agent: Any, guard: Any) -> int:
    base_model = _get_langchain_base_model(agent)
    if base_model is None:
        return 0

    target = _unwrap_langchain_llm_target(base_model)
    if target is None:
        return 0

    return _patch_langchain_concrete_llm(target, guard)


def _get_langchain_model_runnable(agent: Any) -> Any | None:
    for owner in (agent, getattr(agent, "builder", None)):
        if owner is None:
            continue
        nodes = getattr(owner, "nodes", None)
        if not isinstance(nodes, dict):
            continue
        model_node = nodes.get("model")
        if model_node is None:
            continue
        runnable = getattr(model_node, "runnable", None)
        if runnable is not None:
            return runnable
    return None


def _get_langchain_base_model(agent: Any) -> Any | None:
    direct_model = getattr(agent, "model", None)
    if direct_model is not None:
        return direct_model

    inner_agent = getattr(agent, "agent", None)
    llm_chain = getattr(inner_agent, "llm_chain", None)
    chain_model = getattr(llm_chain, "llm", None)
    if chain_model is not None:
        return chain_model

    runnable = _get_langchain_model_runnable(agent)
    if runnable is None:
        return None

    for attr in ("func", "afunc"):
        fn = getattr(runnable, attr, None)
        model = _extract_langchain_closure_model(fn)
        if model is not None:
            return model

    return None


def _extract_langchain_closure_model(fn: Any) -> Any | None:
    if not callable(fn):
        return None

    closure = getattr(fn, "__closure__", None)
    code = getattr(fn, "__code__", None)
    if not closure or code is None:
        return None

    for name, cell in zip(code.co_freevars, closure):
        if name != "model":
            continue
        try:
            return cell.cell_contents
        except ValueError:
            return None
    return None


def _patch_langchain_concrete_llm(model: Any, guard: Any) -> int:
    target = _unwrap_langchain_llm_target(model)
    if target is None:
        return 0

    patched = 0
    for attr in ("invoke", "ainvoke"):
        fn = getattr(target, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        wrapped = _make_guarded_langchain_llm_method(guard, fn, owner=target, label=attr)
        if set_attr(target, attr, wrapped):
            patched += 1
    return patched


def _unwrap_langchain_llm_target(model: Any) -> Any | None:
    seen: set[int] = set()
    current = model
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        inner = getattr(current, "bound", None)
        if inner is None or inner is current:
            return current
        current = inner
    return current


def _make_guarded_langchain_llm_method(
    guard: Any,
    fn: Any,
    *,
    owner: Any,
    label: str,
) -> Any:
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                _guard_langchain_input(
                    guard,
                    owner=owner,
                    label=label,
                    args=args,
                    kwargs=kwargs,
                )
                raw = await fn(*args, **kwargs)
                return _guard_langchain_output(guard, owner=owner, label=label, raw=raw)
            except Exception:
                _sync_local_cache_now(guard, reason="client_error")
                raise
            finally:
                _sync_local_cache_async(guard, reason="round_complete")

        return mark_guarded(async_wrapper)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            _guard_langchain_input(
                guard,
                owner=owner,
                label=label,
                args=args,
                kwargs=kwargs,
            )
            raw = fn(*args, **kwargs)
            return _guard_langchain_output(guard, owner=owner, label=label, raw=raw)
        except Exception:
            _sync_local_cache_now(guard, reason="client_error")
            raise
        finally:
            _sync_local_cache_async(guard, reason="round_complete")

    return mark_guarded(wrapper)


def _guard_langchain_input(
    guard: Any,
    *,
    owner: Any,
    label: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> None:
    payload = _normalize_langchain_request(args, kwargs)
    meta = {
        "adapter": "langchain",
        "label": label,
        "owner_type": type(owner).__name__,
        "owner_module": type(owner).__module__,
    }
    guard.runtime.guard(ev.llm_input(guard.context, payload, **meta))


def _guard_langchain_output(guard: Any, *, owner: Any, label: str, raw: Any) -> Any:
    meta = {
        "adapter": "langchain",
        "label": label,
        "owner_type": type(owner).__name__,
        "owner_module": type(owner).__module__,
    }
    decision = guard.runtime.guard(
        ev.llm_output(guard.context, _normalize_langchain_value(raw), **meta),
        phase="after",
    ).decision
    if decision.decision_type == DecisionType.DENY:
        return {"agentguard": "blocked", "reason": decision.reason}
    if decision.decision_type == DecisionType.SANITIZE:
        return {"agentguard": "sanitized", "reason": decision.reason}
    return raw


def _normalize_langchain_request(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    model_input = kwargs.get("input")
    if model_input is None and args:
        model_input = args[0]

    payload: dict[str, Any] = {
        "input": _normalize_langchain_value(model_input),
    }
    if "config" in kwargs:
        payload["config"] = _normalize_langchain_value(kwargs["config"])
    if "stop" in kwargs:
        payload["stop"] = _normalize_langchain_value(kwargs["stop"])

    extra_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key not in {"input", "config", "stop"}
    }
    if extra_kwargs:
        payload["kwargs"] = _normalize_langchain_value(extra_kwargs)
    return payload


def _normalize_langchain_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _normalize_langchain_value(v) for k, v in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize_langchain_value(v) for v in value]

    message_serializer = _get_langchain_message_serializer()
    if message_serializer is not None:
        try:
            return message_serializer(value)
        except Exception:
            pass

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump()
        except Exception:
            pass

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict()
        except Exception:
            pass

    content = getattr(value, "content", None)
    if content is not None:
        normalized: dict[str, Any] = {
            "type": value.__class__.__name__,
            "content": _normalize_langchain_value(content),
        }
        for attr in ("name", "id", "tool_calls", "invalid_tool_calls", "response_metadata"):
            attr_value = getattr(value, attr, None)
            if attr_value:
                normalized[attr] = _normalize_langchain_value(attr_value)
        return normalized

    return repr(value)


@functools.lru_cache(maxsize=1)
def _get_langchain_message_serializer() -> Any:
    try:
        from langchain_core.messages import message_to_dict
    except Exception:
        return None
    return message_to_dict


def _sync_local_cache_now(guard: Any, *, reason: str) -> None:
    runtime = getattr(guard, "runtime", None)
    sync = getattr(runtime, "sync_local_cache_now", None)
    if callable(sync):
        sync(reason=reason)


def _sync_local_cache_async(guard: Any, *, reason: str) -> None:
    runtime = getattr(guard, "runtime", None)
    sync = getattr(runtime, "sync_local_cache_async", None)
    if callable(sync):
        sync(reason=reason)


def _patch_tool_object(tool: Any, guard: Any, *, name: str) -> int:
    if tool is None or is_guarded(tool):
        return 0

    # Prefer raw tool callables so AgentGuard sees business arguments such as
    # ``path``/``url`` instead of LangChain's generic ``input`` envelope.
    if _patch_tool_attrs(tool, guard, name=name, attrs=("func", "coroutine")):
        return 1
    if _patch_tool_attrs(tool, guard, name=name, attrs=("_run", "_arun")):
        return 1
    if _patch_tool_attrs(tool, guard, name=name, attrs=("invoke", "ainvoke")):
        return 1
    return 0


def _patch_tool_attrs(
    tool: Any,
    guard: Any,
    *,
    name: str,
    attrs: tuple[str, ...],
) -> bool:
    patched = False
    for attr in attrs:
        fn = getattr(tool, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        if attr in {"invoke", "ainvoke"}:
            wrapped = _make_guarded_langchain_tool_method(
                guard,
                fn,
                name=name,
                tool=tool,
            )
        else:
            wrapped = make_guarded_tool(guard, fn, name=name, tool=tool)
        if set_attr(tool, attr, wrapped):
            patched = True
    return patched


def _make_guarded_langchain_tool_method(
    guard: Any,
    fn: Any,
    *,
    name: str,
    tool: Any,
) -> Any:
    metadata = register_tool_metadata(guard, fn, name=name, tool=tool)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                arguments = bind_arguments(fn, args, kwargs)
                decision = guard_tool_before(guard, metadata, arguments)
                blocked = _blocked_langchain_tool_value(decision, metadata.name, args, kwargs)
                if blocked is not None:
                    return blocked
                try:
                    value = await fn(*args, **kwargs)
                except Exception as exc:
                    guard_tool_after(guard, metadata.name, error=str(exc))
                    raise
                result_decision = guard_tool_after(guard, metadata.name, value)
                result_blocked = _blocked_langchain_result_value(
                    result_decision,
                    metadata.name,
                    args,
                    kwargs,
                )
                return result_blocked if result_blocked is not None else value
            except Exception:
                _sync_local_cache_now(guard, reason="client_error")
                raise
            finally:
                _sync_local_cache_async(guard, reason="round_complete")

        return mark_guarded(async_wrapper)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            arguments = bind_arguments(fn, args, kwargs)
            decision = guard_tool_before(guard, metadata, arguments)
            blocked = _blocked_langchain_tool_value(decision, metadata.name, args, kwargs)
            if blocked is not None:
                return blocked
            try:
                value = fn(*args, **kwargs)
            except Exception as exc:
                guard_tool_after(guard, metadata.name, error=str(exc))
                raise
            result_decision = guard_tool_after(guard, metadata.name, value)
            result_blocked = _blocked_langchain_result_value(
                result_decision,
                metadata.name,
                args,
                kwargs,
            )
            return result_blocked if result_blocked is not None else value
        except Exception:
            _sync_local_cache_now(guard, reason="client_error")
            raise
        finally:
            _sync_local_cache_async(guard, reason="round_complete")

    return mark_guarded(wrapper)


def _blocked_langchain_tool_value(
    decision: GuardDecision,
    tool_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return _langchain_tool_message(
            {
                "agentguard": "blocked",
                "tool": tool_name,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            },
            tool_name=tool_name,
            args=args,
            kwargs=kwargs,
        )
    if decision.requires_user or decision.requires_remote:
        return _langchain_tool_message(
            {
                "agentguard": "pending",
                "tool": tool_name,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            },
            tool_name=tool_name,
            args=args,
            kwargs=kwargs,
        )
    if decision.decision_type == DecisionType.DEGRADE:
        return _langchain_tool_message(
            {
                "agentguard": "degraded",
                "tool": tool_name,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            },
            tool_name=tool_name,
            args=args,
            kwargs=kwargs,
        )
    return None


def _blocked_langchain_result_value(
    decision: GuardDecision,
    tool_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any | None:
    if decision.decision_type == DecisionType.DENY:
        return _langchain_tool_message(
            {
                "agentguard": "blocked",
                "tool": tool_name,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            },
            tool_name=tool_name,
            args=args,
            kwargs=kwargs,
        )
    if decision.decision_type == DecisionType.SANITIZE:
        return _langchain_tool_message(
            {
                "agentguard": "sanitized",
                "tool": tool_name,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            },
            tool_name=tool_name,
            args=args,
            kwargs=kwargs,
        )
    if decision.requires_user or decision.requires_remote:
        return _langchain_tool_message(
            {
                "agentguard": "pending",
                "tool": tool_name,
                "reason": decision.reason,
                "decision": decision.decision_type.value,
            },
            tool_name=tool_name,
            args=args,
            kwargs=kwargs,
        )
    return None


def _langchain_tool_message(
    payload: dict[str, Any],
    *,
    tool_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    content = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    tool_call = _extract_langchain_tool_call(args, kwargs)
    tool_call_id = tool_call.get("id") if isinstance(tool_call, dict) else None
    tool_message_cls = _get_langchain_tool_message_class()
    if tool_call_id and tool_message_cls is not None:
        try:
            return tool_message_cls(content=content, name=tool_name, tool_call_id=tool_call_id)
        except Exception:
            return content
    return content


def _extract_langchain_tool_call(
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any] | None:
    candidates = list(args)
    if "input" in kwargs:
        candidates.append(kwargs["input"])
    for value in candidates:
        if not isinstance(value, dict):
            continue
        if value.get("type") == "tool_call" and value.get("name"):
            return value
    return None


@functools.lru_cache(maxsize=1)
def _get_langchain_tool_message_class() -> Any:
    try:
        from langchain_core.messages import ToolMessage
    except Exception:
        return None
    return ToolMessage
