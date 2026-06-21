"""Best-effort framework patch helpers for native agent loops."""
from __future__ import annotations

import functools
import inspect
from typing import Any, Callable

from agentguard.schemas import events as ev
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.tools.metadata import ToolMetadata

_PATCHED_ATTR = "__agentguard_patched__"
_WRAPPED_ATTR = "__agentguard_wrapped__"


def is_guarded(obj: Any) -> bool:
    return bool(getattr(obj, _PATCHED_ATTR, False) or getattr(obj, _WRAPPED_ATTR, False))


def mark_guarded(obj: Any) -> Any:
    try:
        setattr(obj, _WRAPPED_ATTR, True)
    except Exception:
        pass
    return obj


def mark_patched(obj: Any) -> None:
    try:
        object.__setattr__(obj, _PATCHED_ATTR, True)
    except Exception:
        try:
            setattr(obj, _PATCHED_ATTR, True)
        except Exception:
            pass


def tool_name(tool: Any, fn: Callable[..., Any] | None = None, fallback: str = "tool") -> str:
    return str(
        getattr(tool, "name", None)
        or getattr(tool, "__name__", None)
        or (getattr(fn, "__name__", None) if fn is not None else None)
        or fallback
    )


def bind_arguments(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)
        return dict(bound.arguments)
    except (TypeError, ValueError):
        out = dict(kwargs)
        if args:
            out["_args"] = list(args)
        return out


def set_attr(obj: Any, attr: str, value: Any) -> bool:
    try:
        object.__setattr__(obj, attr, value)
        return True
    except Exception:
        try:
            setattr(obj, attr, value)
            return True
        except Exception:
            return False


def register_tool_metadata(
    guard: Any,
    fn: Callable[..., Any],
    *,
    name: str,
    tool: Any = None,
    capabilities: list[str] | None = None,
) -> ToolMetadata:
    desc = getattr(tool, "description", None) or getattr(tool, "__doc__", None)
    caps = capabilities if capabilities is not None else getattr(tool, "capabilities", None)
    if caps is None:
        caps = []
    return guard.register_tool(
        fn,
        name=name,
        description=str(desc).strip().split("\n")[0] if desc else "",
        capabilities=list(caps),
    )


def guard_llm_before(
    guard: Any,
    *,
    label: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> GuardDecision:
    request = {"label": label, "args": list(args), "kwargs": dict(kwargs)}
    return guard.runtime.guard(ev.llm_input(guard.context, request)).decision


def guard_llm_after(guard: Any, output: Any) -> GuardDecision:
    return guard.runtime.guard(ev.llm_output(guard.context, output), phase="after").decision


def guard_tool_before(
    guard: Any,
    metadata: ToolMetadata,
    arguments: dict[str, Any],
) -> GuardDecision:
    return guard.runtime.guard(
        ev.tool_invoke(
            guard.context,
            metadata.name,
            arguments,
            capabilities=list(metadata.capabilities),
        )
    ).decision


def guard_tool_after(
    guard: Any,
    tool_name: str,
    result: Any = None,
    *,
    error: str | None = None,
) -> GuardDecision:
    return guard.runtime.guard(
        ev.tool_result(guard.context, tool_name, result, error=error),
        phase="after",
    ).decision


def make_guarded_tool(
    guard: Any,
    fn: Callable[..., Any],
    *,
    name: str,
    tool: Any = None,
    capabilities: list[str] | None = None,
) -> Callable[..., Any]:
    """Return a guarded callable compatible with sync and async framework tools."""
    if is_guarded(fn):
        return fn

    metadata = register_tool_metadata(
        guard, fn, name=name, tool=tool, capabilities=capabilities
    )

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                arguments = bind_arguments(fn, args, kwargs)
                decision = guard_tool_before(guard, metadata, arguments)
                blocked = _blocked_tool_value(decision, metadata.name)
                if blocked is not None:
                    return blocked
                try:
                    value = await fn(*args, **kwargs)
                except Exception as exc:
                    guard_tool_after(guard, metadata.name, error=str(exc))
                    raise
                result_decision = guard_tool_after(guard, metadata.name, value)
                result_blocked = _blocked_result_value(result_decision, metadata.name)
                return result_blocked if result_blocked is not None else value
            except Exception:
                _sync_local_cache_now(guard, reason="client_error")
                raise
            finally:
                _sync_local_cache_async(guard, reason="round_complete")

        return mark_guarded(async_wrapper)

    wrapped = guard.wrap_tool(
        fn,
        name=metadata.name,
        description=metadata.description,
        capabilities=list(metadata.capabilities),
    )
    return mark_guarded(wrapped)


def make_guarded_llm_callable(
    guard: Any,
    fn: Callable[..., Any],
    *,
    label: str,
) -> Callable[..., Any]:
    """Wrap a concrete LLM call method without replacing the provider object."""
    if is_guarded(fn):
        return fn

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                before_decision = guard_llm_before(guard, label=label, args=args, kwargs=kwargs)
                before_blocked = _blocked_llm_value(before_decision)
                if before_blocked is not None:
                    return before_blocked
                raw = await fn(*args, **kwargs)
                decision = guard_llm_after(guard, raw)
                blocked = _blocked_llm_value(decision)
                return blocked if blocked is not None else raw
            except Exception:
                _sync_local_cache_now(guard, reason="client_error")
                raise
            finally:
                _sync_local_cache_async(guard, reason="round_complete")

        return mark_guarded(async_wrapper)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            before_decision = guard_llm_before(guard, label=label, args=args, kwargs=kwargs)
            before_blocked = _blocked_llm_value(before_decision)
            if before_blocked is not None:
                return before_blocked
            raw = fn(*args, **kwargs)
            decision = guard_llm_after(guard, raw)
            blocked = _blocked_llm_value(decision)
            return blocked if blocked is not None else raw
        except Exception:
            _sync_local_cache_now(guard, reason="client_error")
            raise
        finally:
            _sync_local_cache_async(guard, reason="round_complete")

    return mark_guarded(wrapper)


def patch_llm_methods(
    guard: Any,
    obj: Any,
    *,
    methods: tuple[str, ...] = (
        "create",
        "complete",
        "completion",
        "generate",
        "invoke",
        "ainvoke",
        "predict",
        "chat",
    ),
) -> int:
    patched = 0
    for name in methods:
        if '.' in name:
            parts = name.split('.')
            fn = obj
            for part in parts[:-1]:
                fn = getattr(fn, part, None)
                if fn is None:
                    break
            fn = getattr(fn, parts[-1], None)
        else:
            fn = getattr(obj, name, None)
        if not callable(fn) or is_guarded(fn):
            continue
        if set_attr(obj, name, make_guarded_llm_callable(guard, fn, label=name)):
            patched += 1
    return patched


def _blocked_tool_value(decision: GuardDecision, tool: str) -> Any | None:
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


def _blocked_result_value(decision: GuardDecision, tool: str) -> Any | None:
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


def _sync_local_cache_now(guard: Any, *, reason: str) -> None:
    rt = getattr(guard, "runtime", None)
    sync = getattr(rt, "sync_local_cache_now", None)
    if callable(sync):
        sync(reason=reason)


def _sync_local_cache_async(guard: Any, *, reason: str) -> None:
    rt = getattr(guard, "runtime", None)
    sync = getattr(rt, "sync_local_cache_async", None)
    if callable(sync):
        sync(reason=reason)
