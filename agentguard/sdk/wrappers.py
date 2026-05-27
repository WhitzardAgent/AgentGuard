"""Decorator / utility that wraps a plain callable into a guarded tool.

Both synchronous and asynchronous (``async def``) callables are supported.

Async execution model
---------------------
For ``async def`` tools the wrapper takes a *native async path* that avoids
blocking the event loop:

1. ``loop.run_in_executor`` offloads the synchronous policy-check (which may
   involve a blocking HTTP call in remote mode) to a thread-pool worker while
   the asyncio event loop stays responsive.
2. After receiving the decision, enforcement (DENY / HUMAN_CHECK / DEGRADE /
   ALLOW + obligations) is applied inline — no sync↔async bridge hack.
3. The underlying coroutine is directly ``await``-ed in the async wrapper.

This replaces the old ``_AsyncNeeded`` BaseException hack which was fragile,
failed to propagate Enforcer arg-rewrites into the actual execution, and could
cause subtle ordering issues with AutoGen ≥ 0.4's task scheduling.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from functools import wraps
from typing import Any, Callable, TYPE_CHECKING

from agentguard.models.events import (
    EventType,
    Principal,
    RuntimeEvent,
    ToolCall,
    ToolStaticLabel,
)
from agentguard.sdk.context import current_session

if TYPE_CHECKING:
    from agentguard.sdk.guard import Guard


def wrap_tool(
    guard: "Guard",
    tool_name: str,
    fn: Callable[..., Any],
    *,
    sink_type: str = "none",
    boundary: str = "internal",
    sensitivity: str = "low",
    integrity: str = "trusted",
    tags: list[str] | None = None,
) -> Callable[..., Any]:
    """Wrap `fn` so every invocation passes through the Guard pipeline.

    Static labels (``boundary``/``sensitivity``/``integrity``/``tags``) are
    declared at registration time and copied verbatim onto every ToolCall.

    Works for both ``def`` and ``async def`` functions.
    """
    sig = inspect.signature(fn)
    is_async = asyncio.iscoroutinefunction(fn)

    # Capture parameter names → exposed as ``tool.<param>`` shortcut paths.
    syntax_fields: list[str] = [
        name for name, p in sig.parameters.items()
        if p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                          inspect.Parameter.VAR_KEYWORD)
    ]

    static_label = ToolStaticLabel(
        boundary=boundary,           # type: ignore[arg-type]
        sensitivity=sensitivity,     # type: ignore[arg-type]
        integrity=integrity,         # type: ignore[arg-type]
        tags=list(tags or []),
    )
    metadata = {
        "tool_name": tool_name,
        "sink_type": sink_type,
        "boundary": boundary,
        "sensitivity": sensitivity,
        "integrity": integrity,
        "tags": list(tags or []),
        "syntax": list(syntax_fields),
    }

    def _build_event(bound: inspect.BoundArguments) -> RuntimeEvent:
        principal, goal, scope = _resolve_principal()
        return RuntimeEvent(
            event_type=EventType.TOOL_CALL_ATTEMPT,
            principal=principal,
            goal=goal,
            scope=list(scope),
            tool_call=ToolCall(
                tool_name=tool_name,
                args=dict(bound.arguments),
                target=_extract_target(tool_name, bound.arguments),
                sink_type=sink_type,  # type: ignore[arg-type]
                label=static_label,
                syntax=list(syntax_fields),
            ),
        )

    if is_async:
        @wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            event = _build_event(bound)
            pipeline = guard.pipeline

            # ── Step 1: policy check ──────────────────────────────────
            # Run in a thread-pool worker so the event loop stays free
            # (critical for remote mode where this makes a blocking HTTP call).
            loop = asyncio.get_running_loop()
            try:
                decision = await loop.run_in_executor(
                    None, pipeline.handle_attempt, event
                )
            except Exception as exc:
                # Guard unavailable: honour fail_open setting
                fail_open = getattr(pipeline, "fail_open", True)
                if not fail_open:
                    from agentguard.models.errors import DecisionDenied
                    raise DecisionDenied(
                        reason=f"guard_unavailable: {exc}",
                        matched_rules=[],
                    ) from exc
                # fail_open → execute without policy check
                return await fn(**dict(bound.arguments))

            # ── Step 2: enforce decision ──────────────────────────────
            from agentguard.models.decisions import Action
            from agentguard.models.errors import DecisionDenied, HumanApprovalPending

            mode = getattr(pipeline, "mode", "enforce")

            if mode == "dry_run":
                return {"agentguard_dry_run": True,
                        "decision": decision.model_dump(mode="json")}

            if mode != "monitor":
                if decision.action is Action.DENY:
                    raise DecisionDenied(
                        reason=decision.reason or "policy_denied",
                        matched_rules=list(decision.matched_rules),
                        request_id=event.event_id,
                    )
                if decision.action is Action.HUMAN_CHECK:
                    raise HumanApprovalPending(
                        ticket_id=f"pending_{uuid.uuid4().hex[:8]}",
                        reason=decision.reason or "human_check_required",
                    )

            # ── Step 3: pre-execution arg transforms (DEGRADE / obligations) ─
            exec_event = event
            if decision.action is Action.DEGRADE or decision.obligations:
                from agentguard.degrade.transformers import ActionExecutor
                rewritten_tc = ActionExecutor().apply_rewrites(exec_event, decision)
                if rewritten_tc and rewritten_tc != exec_event.tool_call:
                    exec_event = exec_event.with_tool_call(rewritten_tc)

                if decision.obligations and mode != "monitor":
                    # Rate-limit and require-target checks (sync but fast)
                    from agentguard.degrade.transformers import ActionExecutor as AX
                    ax = AX()
                    rate_violation = ax.check_rate_limit(exec_event, decision)
                    if rate_violation:
                        raise DecisionDenied(
                            reason=f"rate_limit: {rate_violation}",
                            matched_rules=list(decision.matched_rules),
                            request_id=event.event_id,
                        )
                    tgt_violation = ax.check_require_target_in(exec_event, decision)
                    if tgt_violation:
                        raise DecisionDenied(
                            reason=f"require_target_in: {tgt_violation}",
                            matched_rules=list(decision.matched_rules),
                            request_id=event.event_id,
                        )

            # ── Step 4: execute the underlying async tool ─────────────
            tc = exec_event.tool_call
            exec_args: dict[str, Any] = dict(tc.args) if tc else dict(bound.arguments)

            # Support tool-redirection (DEGRADE may swap to a different tool)
            target_name = tc.tool_name if tc else tool_name
            if target_name != tool_name and target_name in guard.registry:
                inner = guard.registry[target_name]
                raw = getattr(inner, "__agentguard_raw__", inner)
                if asyncio.iscoroutinefunction(raw):
                    result = await raw(**exec_args)
                else:
                    result = await loop.run_in_executor(None, lambda: raw(**exec_args))
            else:
                result = await fn(**exec_args)

            # ── Step 5: back-fill result for post-exec rule evaluation ─
            if exec_event.tool_call is not None:
                try:
                    exec_event.tool_call.result = result
                except Exception:
                    pass

            # ── Step 6: update rich trace (in-process mode only) ──────
            if hasattr(pipeline, "_cache"):
                from agentguard.runtime.enrichment import update_trace_result
                update_trace_result(exec_event, pipeline._cache, result)

            # ── Step 7: post-execution audit / graph ──────────────────
            result_event = exec_event.model_copy(
                update={"event_type": EventType.TOOL_CALL_RESULT}
            )
            pipeline.handle_result(result_event)

            return result

        async_wrapper.__agentguard__ = metadata  # type: ignore[attr-defined]
        async_wrapper.__agentguard_raw__ = fn  # type: ignore[attr-defined]
        async_wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        return async_wrapper

    # Synchronous path (original behaviour, preserved exactly)
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = sig.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        event = _build_event(bound)

        def executor(current_event: RuntimeEvent) -> Any:
            tc = current_event.tool_call
            if tc is None:
                return fn(**bound.arguments)
            target_tool = tc.tool_name
            rewritten_args = dict(tc.args)
            if target_tool != tool_name and target_tool in guard.registry:
                inner = guard.registry[target_tool]
                raw = getattr(inner, "__agentguard_raw__", inner)
                result = raw(**rewritten_args)
            else:
                result = fn(**rewritten_args)
            # Stash the result on the ToolCall so post-execution rules
            # (tool_call.completed) can access ``tool.result``.
            try:
                tc.result = result
            except Exception:
                pass
            return result

        return guard.pipeline.guarded_call(event, executor)

    wrapper.__agentguard__ = metadata  # type: ignore[attr-defined]
    wrapper.__agentguard_raw__ = fn  # type: ignore[attr-defined]
    wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
    return wrapper


def _resolve_principal() -> tuple[Principal, str | None, list[str]]:
    session = current_session()
    if session is not None:
        return session.principal, session.goal, session.scope
    return Principal(agent_id="sdk-default", session_id="anon"), None, []


def _extract_target(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    target: dict[str, Any] = {}
    if "url" in args:
        import urllib.parse
        try:
            parsed = urllib.parse.urlparse(str(args["url"]))
            target["url"] = args["url"]
            target["domain"] = parsed.hostname or ""
        except Exception:
            target["url"] = args["url"]
    if "to" in args and tool_name.startswith("email"):
        to_val = args["to"]
        if isinstance(to_val, str) and "@" in to_val:
            target["domain"] = to_val.split("@", 1)[1]
        elif isinstance(to_val, (list, tuple)) and to_val:
            first = str(to_val[0])
            if "@" in first:
                target["domain"] = first.split("@", 1)[1]
    if "path" in args:
        target["path"] = args["path"]
    return target
