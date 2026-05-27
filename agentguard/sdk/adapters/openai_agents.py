"""Adapter for the OpenAI Agents SDK (``openai-agents`` package).

The SDK represents tools as :class:`FunctionTool` objects whose
``on_invoke_tool`` callable is invoked by the Runner as::

    result: str = await tool.on_invoke_tool(run_context, json_input_str)

Note the ``await``: the SDK **always** awaits ``on_invoke_tool``, so the
replacement must be an ``async def``.  A sync replacement would be called,
return a plain string, and the SDK would try to await that string — which
raises ``TypeError: object str can't be used in 'await' expression``.

A subtler failure (the original bug) occurs when the *original*
``on_invoke_tool`` is itself ``async``: calling it without ``await`` returns
a coroutine object, which Pydantic cannot serialize:
``PydanticSerializationError: Unable to serialize unknown type: <class 'coroutine'>``.

The fix: ``guarded_invoke`` is now ``async def``, uses the same
``loop.run_in_executor`` pattern as the AutoGen adapter for the blocking
policy check, and properly ``await``s the original when it is a coroutine
function.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from agentguard.models.decisions import Action
from agentguard.models.errors import DecisionDenied, HumanApprovalPending
from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall
from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.context import current_session
from agentguard.sdk.wrappers import _extract_target, wrap_tool

log = logging.getLogger(__name__)


def _infer_sink(tool_name: str) -> str:
    for prefix, sink in [
        ("email", "email"), ("mail", "email"),
        ("http", "http"),   ("browser", "http"),
        ("shell", "shell"),
        ("fs", "fs_write"), ("file", "fs_write"),
        ("db", "db_write"), ("sql", "db_write"),
    ]:
        if tool_name.startswith(prefix):
            return sink
    return "none"


class OpenAIAgentsAdapter(BaseAdapter):
    """Intercept OpenAI Agents SDK tool calls before they execute.

    Supports:
    * **FunctionTool list** — ``agent.tools = [FunctionTool(...), ...]``
      (real ``openai-agents`` SDK shape).  The ``on_invoke_tool``
      callable is replaced with a guarded wrapper that receives
      ``(run_context, json_str)`` and builds a ``RuntimeEvent`` from
      the parsed JSON args.
    * **Plain dict** — ``agent.tools = {"name": fn}``
      (legacy / duck-typed usage).  Behaves like the old stub.
    """

    def install(self, framework_obj: Any) -> None:
        tools = getattr(framework_obj, "tools", None)
        if isinstance(tools, (list, tuple)):
            for t in tools:
                if _is_function_tool(t):
                    self._wrap_function_tool(t)
                elif callable(t) and not getattr(t, "__agentguard__", None):
                    # bare callable (plain function/lambda) registered directly
                    name = getattr(t, "__name__", "unknown_tool")
                    wrapped = wrap_tool(self.guard, name, t)
                    self.guard._record_tool_registration(name, wrapped)
        elif isinstance(tools, dict):
            for name, fn in list(tools.items()):
                if callable(fn) and not getattr(fn, "__agentguard__", None):
                    tools[name] = wrap_tool(self.guard, name, fn)
                    self.guard._record_tool_registration(name, tools[name])
        else:
            log.warning(
                "OpenAIAgentsAdapter: expected agent.tools to be a list or dict, "
                "got %r — nothing patched.", type(tools)
            )

    # ── FunctionTool path ────────────────────────────────────────────

    def _wrap_function_tool(self, tool: Any) -> None:
        """Replace ``tool.on_invoke_tool`` with an async guarded callable.

        The OpenAI Agents SDK always ``await``s ``on_invoke_tool``, so the
        replacement *must* be an ``async def``.  The replacement:

        1. Runs the synchronous policy check in a thread-pool worker so the
           event loop stays responsive (important when guard is in remote
           mode and the check involves an HTTP round-trip).
        2. Enforces the decision inline (DENY → raise, DEGRADE → rewrite args,
           ALLOW → fall through).
        3. Calls the *original* ``on_invoke_tool``; if the original is itself
           async (the common case with real SDK tools), it is properly
           ``await``-ed — fixing the coroutine-serialization crash.
        """
        original = tool.on_invoke_tool
        if getattr(original, "__agentguard__", None):
            return  # already wrapped

        tool_name: str = getattr(tool, "name", None) or getattr(
            original, "__name__", "unknown_tool"
        )
        guard = self.guard
        # Pre-check at wrap time; we also do a runtime fallback below.
        orig_is_async: bool = asyncio.iscoroutinefunction(original)
        log.debug(
            "OpenAIAgentsAdapter: %r orig_is_async=%s", tool_name, orig_is_async
        )

        async def guarded_invoke(run_ctx: Any, json_input: str) -> str:
            # ── Parse JSON args ───────────────────────────────────────
            try:
                args: dict[str, Any] = json.loads(json_input) if json_input else {}
                if not isinstance(args, dict):
                    args = {"value": args}
            except Exception:
                args = {"raw_input": json_input}

            # ── Resolve principal ─────────────────────────────────────
            sess = current_session()
            if sess is not None:
                principal = sess.principal
                goal = sess.goal
                scope = list(sess.scope)
            else:
                principal = Principal(agent_id="openai-agent", session_id="anon")
                goal = None
                scope = []

            event = RuntimeEvent(
                event_type=EventType.TOOL_CALL_ATTEMPT,
                principal=principal,
                goal=goal,
                scope=scope,
                tool_call=ToolCall(
                    tool_name=tool_name,
                    args=args,
                    target=_extract_target(tool_name, args),
                    sink_type=_infer_sink(tool_name),  # type: ignore[arg-type]
                ),
            )

            # ── Policy check (non-blocking) ───────────────────────────
            loop = asyncio.get_running_loop()
            try:
                decision = await loop.run_in_executor(
                    None, guard.pipeline.handle_attempt, event
                )
            except Exception as exc:
                fail_open = getattr(guard.pipeline, "fail_open", True)
                if not fail_open:
                    raise DecisionDenied(
                        reason=f"guard_unavailable: {exc}",
                        matched_rules=[],
                    ) from exc
                decision = None  # fail-open: skip enforcement

            # ── Enforce decision ──────────────────────────────────────
            exec_event = event
            if decision is not None:
                mode = getattr(guard.pipeline, "mode", "enforce")
                if mode not in ("monitor", "dry_run"):
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
                    if decision.action is Action.DEGRADE or decision.obligations:
                        from agentguard.degrade.transformers import ActionExecutor
                        rewritten_tc = ActionExecutor().apply_rewrites(exec_event, decision)
                        if rewritten_tc and rewritten_tc != exec_event.tool_call:
                            exec_event = exec_event.with_tool_call(rewritten_tc)

            # ── Execute the original on_invoke_tool ───────────────────
            actual_args = dict(exec_event.tool_call.args) if exec_event.tool_call else args
            raw_input = json.dumps(actual_args)

            # Call the original — then check what we actually got back.
            # We cannot rely solely on the pre-computed `orig_is_async` flag
            # because some SDKs store `on_invoke_tool` as a closure or partial
            # whose coroutine nature is not always detectable at wrap time.
            raw_call = original(run_ctx, raw_input)

            if asyncio.iscoroutine(raw_call) or asyncio.isfuture(raw_call):
                # Async original — properly await it
                result: Any = await raw_call
            elif orig_is_async and not asyncio.iscoroutine(raw_call):
                # Detected async at wrap time but got a plain value?
                # (defensive — shouldn't happen, but safe to handle)
                result = raw_call
            else:
                result = raw_call

            # ── Back-fill result for post-exec rules ──────────────────
            if exec_event.tool_call is not None:
                try:
                    exec_event.tool_call.result = result
                except Exception:
                    pass

            # ── Update rich trace (in-process mode) ───────────────────
            if hasattr(guard.pipeline, "_cache"):
                from agentguard.runtime.enrichment import update_trace_result
                update_trace_result(exec_event, guard.pipeline._cache, result)

            # ── Post-execution audit ──────────────────────────────────
            result_event = exec_event.model_copy(
                update={"event_type": EventType.TOOL_CALL_RESULT}
            )
            guard.pipeline.handle_result(result_event)

            return result if isinstance(result, str) else json.dumps(result)

        guarded_invoke.__agentguard__ = {"tool_name": tool_name}  # type: ignore[attr-defined]
        try:
            object.__setattr__(tool, "on_invoke_tool", guarded_invoke)
        except (AttributeError, TypeError):
            tool.on_invoke_tool = guarded_invoke
        self.guard._record_tool_registration(tool_name, guarded_invoke)
        log.debug("OpenAIAgentsAdapter: wrapped FunctionTool %r.", tool_name)


def _is_function_tool(obj: Any) -> bool:
    """True if *obj* looks like an openai-agents FunctionTool.

    Accepts any object that has both ``on_invoke_tool`` and ``name``
    attributes, regardless of whether the object is itself callable.

    Earlier versions of the check required ``not callable(obj)``, but
    some versions of the real openai-agents SDK define ``__call__`` on
    FunctionTool, which made the guard silently skip wrapping.
    """
    return hasattr(obj, "on_invoke_tool") and hasattr(obj, "name")
