"""LangChain agent adapter (best-effort, optional dependency)."""
from __future__ import annotations

import functools
import inspect
from collections.abc import Sequence
from typing import Any

from agentguard.adapters.agent.base import BaseAgentAdapter
from agentguard.adapters.agent.patching import (
    is_guarded,
    mark_guarded,
    make_guarded_tool,
    patch_llm_methods,
    set_attr,
    tool_name,
)
from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType
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

    def attach(
        self,
        agent: Any,
        guard: Any,
        *,
        wrap_tools: bool = True,
        wrap_llm: bool = True,
    ) -> dict[str, Any]:
        """Patch LangChain/LangGraph tool and model call sites in-place."""
        patched = {"tools": 0, "llm": 0}
        if wrap_tools:
            patched["tools"] += self._patch_tool_containers(agent, guard)
        if wrap_llm:
            patched["llm"] += self._patch_llm(agent, guard)
        return patched

    def _patch_tool_containers(self, agent: Any, guard: Any) -> int:
        patched = 0
        patched += _patch_container_tools(agent, guard)

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

    def _patch_llm(self, agent: Any, guard: Any) -> int:
        return _patch_langchain_llm(agent, guard)


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


def _patch_langchain_llm(agent: Any, guard: Any) -> int:
    base_model = _get_langchain_base_model(agent)
    if base_model is None:
        return 0

    target = _unwrap_langchain_llm_target(base_model)
    if target is None:
        return 0

    patched = _patch_langchain_provider_clients(target, guard)
    if patched:
        return patched

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


def _capture_langchain_call_target(guard: Any, *, label: str, target: Any) -> None:
    try:
        calls = getattr(guard, "_agentguard_langchain_call_targets", None)
        if not isinstance(calls, dict):
            calls = {}
            setattr(guard, "_agentguard_langchain_call_targets", calls)
        calls[label] = target
    except Exception:
        pass


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


def _patch_langchain_provider_clients(model: Any, guard: Any) -> int:
    provider = _detect_langchain_provider(model)
    if provider == "openai":
        return _patch_langchain_openai_provider(model, guard)
    if provider == "anthropic":
        return _patch_langchain_anthropic_provider(model, guard)
    return 0


def _detect_langchain_provider(model: Any) -> str | None:
    class_name = type(model).__name__.lower()
    module_name = type(model).__module__.lower()

    if "openai" in module_name or "openai" in class_name:
        return "openai"
    if "anthropic" in module_name or "anthropic" in class_name:
        return "anthropic"
    return None


def _patch_langchain_openai_provider(model: Any, guard: Any) -> int:
    patched = 0
    seen: set[int] = set()
    for attr in ("client", "async_client", "root_client", "root_async_client"):
        inner = getattr(model, attr, None)
        if inner is None or id(inner) in seen:
            continue
        seen.add(id(inner))
        patched += _patch_langchain_openai_candidate(
            guard,
            inner,
            label=f"{type(model).__name__}.{attr}",
        )
    return patched


def _patch_langchain_openai_candidate(guard: Any, candidate: Any, *, label: str) -> int:
    patched = 0

    if callable(getattr(candidate, "create", None)):
        _capture_langchain_call_target(guard, label=label, target=candidate)
        patched += patch_llm_methods(guard, candidate, methods=("create",))

    if callable(getattr(candidate, "parse", None)):
        _capture_langchain_call_target(guard, label=f"{label}.parse", target=candidate)
        patched += patch_llm_methods(guard, candidate, methods=("parse",))

    raw_candidate = getattr(candidate, "with_raw_response", None)
    if raw_candidate is not None:
        _capture_langchain_call_target(guard, label=f"{label}.with_raw_response", target=raw_candidate)
        patched += patch_llm_methods(guard, raw_candidate, methods=("create", "parse"))

    chat = getattr(candidate, "chat", None)
    completions = getattr(chat, "completions", None) if chat is not None else None
    if completions is not None:
        _capture_langchain_call_target(
            guard,
            label=f"{label}.chat.completions",
            target=completions,
        )
        patched += patch_llm_methods(guard, completions, methods=("create", "parse"))

        raw = getattr(completions, "with_raw_response", None)
        if raw is not None:
            _capture_langchain_call_target(
                guard,
                label=f"{label}.chat.completions.with_raw_response",
                target=raw,
            )
            patched += patch_llm_methods(guard, raw, methods=("create", "parse"))

    responses = getattr(candidate, "responses", None)
    if responses is not None:
        _capture_langchain_call_target(guard, label=f"{label}.responses", target=responses)
        patched += patch_llm_methods(guard, responses, methods=("create", "parse"))

        raw = getattr(responses, "with_raw_response", None)
        if raw is not None:
            _capture_langchain_call_target(
                guard,
                label=f"{label}.responses.with_raw_response",
                target=raw,
            )
            patched += patch_llm_methods(guard, raw, methods=("create", "parse"))

    beta = getattr(candidate, "beta", None)
    beta_chat = getattr(beta, "chat", None) if beta is not None else None
    beta_completions = getattr(beta_chat, "completions", None) if beta_chat is not None else None
    if beta_completions is not None:
        _capture_langchain_call_target(
            guard,
            label=f"{label}.beta.chat.completions",
            target=beta_completions,
        )
        patched += patch_llm_methods(guard, beta_completions, methods=("create", "parse", "stream"))

    return patched


def _patch_langchain_anthropic_provider(model: Any, guard: Any) -> int:
    patched = 0
    seen: set[int] = set()
    for attr in ("_client", "_async_client"):
        inner = getattr(model, attr, None)
        if inner is None or id(inner) in seen:
            continue
        seen.add(id(inner))
        patched += _patch_langchain_anthropic_candidate(
            guard,
            inner,
            label=f"{type(model).__name__}.{attr}",
        )
    return patched


def _patch_langchain_anthropic_candidate(guard: Any, candidate: Any, *, label: str) -> int:
    patched = 0

    messages = getattr(candidate, "messages", None)
    if messages is not None:
        _capture_langchain_call_target(guard, label=f"{label}.messages", target=messages)
        patched += patch_llm_methods(guard, messages, methods=("create", "stream"))

    beta = getattr(candidate, "beta", None)
    beta_messages = getattr(beta, "messages", None) if beta is not None else None
    if beta_messages is not None:
        _capture_langchain_call_target(
            guard,
            label=f"{label}.beta.messages",
            target=beta_messages,
        )
        patched += patch_llm_methods(guard, beta_messages, methods=("create", "stream"))

    return patched


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

    patched = 0
    for attr in ("func", "coroutine", "_run", "_arun"):
        fn = getattr(tool, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        wrapped = make_guarded_tool(guard, fn, name=name, tool=tool)
        if set_attr(tool, attr, wrapped):
            patched += 1
    if patched:
        return 1

    for attr in ("invoke", "ainvoke"):
        fn = getattr(tool, attr, None)
        if not callable(fn) or is_guarded(fn):
            continue
        wrapped = make_guarded_tool(guard, fn, name=name, tool=tool)
        if set_attr(tool, attr, wrapped):
            patched += 1
    return 1 if patched else 0
