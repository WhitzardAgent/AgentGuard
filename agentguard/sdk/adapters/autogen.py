"""Adapter for Microsoft AutoGen-style agents.

Supports:
- AutoGen ≤ 0.2  (``function_map`` / ``register_function``)
- AutoGen  0.3   (``_tools`` list with public ``.func`` attribute)
- AutoGen ≥ 0.4  (``_tools`` list with **private** ``._func`` attribute on
                   ``FunctionTool``, or objects exposing ``run_json``)

Root-cause note
~~~~~~~~~~~~~~~
AutoGen ≥ 0.4 stores the underlying Python callable in ``FunctionTool._func``
(private underscore).  The previous version of this adapter only probed the
public ``func`` attribute, so the guard was never wrapping — or intercepting —
any tool call when running on AutoGen 0.4+.  The fix is to probe both names
and to fall back to patching ``run_json`` for any tool object that doesn't
expose either.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from agentguard.sdk.adapters.base import BaseAdapter
from agentguard.sdk.wrappers import wrap_tool

log = logging.getLogger(__name__)

# Attribute names used by different AutoGen versions to store the underlying fn.
_FUNC_ATTRS = ("func", "_func")


def _extract_fn(tool: Any) -> tuple[Any, str | None]:
    """Return (callable, attr_name_or_None) for the underlying function in *tool*.

    Probes public ``func`` first (AutoGen ≤ 0.3), then private ``_func``
    (AutoGen ≥ 0.4).  Returns ``(None, None)`` if no function is found.
    """
    for attr in _FUNC_ATTRS:
        candidate = getattr(tool, attr, None)
        if callable(candidate) and not getattr(candidate, "__agentguard__", None):
            return candidate, attr
    return None, None


class AutogenAdapter(BaseAdapter):
    def install(self, framework_obj: Any) -> None:
        # ── AutoGen ≥ 0.4: tools stored as a list ──────────────────────
        tools_list = getattr(framework_obj, "_tools", None)
        if isinstance(tools_list, list) and tools_list:
            self._patch_tools_list(framework_obj, tools_list)
            return

        # ── AutoGen ≤ 0.2: function_map dict ───────────────────────────
        registry = getattr(framework_obj, "function_map", None)
        if isinstance(registry, dict):
            self._patch_function_map(registry)
            return

        # ── Fallback: patch register_function hook ──────────────────────
        if hasattr(framework_obj, "register_function"):
            self._patch_register_function(framework_obj)

    # ── AutoGen ≥ 0.4 path ─────────────────────────────────────────────

    def _patch_tools_list(self, agent: Any, tools_list: list[Any]) -> None:
        """Wrap callables stored in agent._tools (v0.4+ AssistantAgent).

        Strategy
        --------
        1. Look for the underlying function in ``.func`` **or** ``._func``
           (covers all known AutoGen 0.x variants).
        2. Patch the attribute in-place so AutoGen's internal ``_run_impl``
           picks up the guarded version.
        3. If neither attribute exists but the tool has a ``run_json`` method
           (BaseTool protocol), monkey-patch ``run_json`` directly as a
           last resort.
        4. If the tool is itself a plain callable (e.g. a lambda or a bare
           ``def``), replace it in the list.
        """
        for i, tool in enumerate(tools_list):
            if getattr(tool, "__agentguard_patched__", False):
                continue

            fn, fn_attr = _extract_fn(tool)

            if fn is not None:
                # ── happy path: found the underlying callable ──────────
                name = (
                    getattr(tool, "name", None)
                    or getattr(fn, "__name__", f"tool_{i}")
                )
                wrapped = wrap_tool(self.guard, name, fn)

                # Patch the attribute back so AutoGen calls the guarded fn.
                patched = False
                for try_attr in (fn_attr,) + tuple(
                    a for a in _FUNC_ATTRS if a != fn_attr
                ):
                    if not hasattr(tool, try_attr):
                        continue
                    try:
                        object.__setattr__(tool, try_attr, wrapped)
                        patched = True
                        break
                    except (AttributeError, TypeError):
                        try:
                            setattr(tool, try_attr, wrapped)
                            patched = True
                            break
                        except Exception:
                            continue

                if not patched:
                    # Could not mutate the tool object (e.g. frozen dataclass).
                    # Replace the entire slot in the list.
                    tools_list[i] = wrapped
                    log.warning(
                        "AutogenAdapter: could not patch %r in-place; replaced "
                        "tools_list[%d] with wrapper.  AutoGen may not handle "
                        "this correctly if it expects a BaseTool instance.",
                        name,
                        i,
                    )

                self.guard._record_tool_registration(name, wrapped)
                try:
                    object.__setattr__(tool, "__agentguard_patched__", True)
                except Exception:
                    pass
                log.debug(
                    "AutogenAdapter: wrapped _tools[%d] %r via attr %r.",
                    i, name, fn_attr,
                )

            elif hasattr(tool, "run_json"):
                # ── fallback: patch BaseTool.run_json ──────────────────
                self._patch_run_json(i, tool)

            elif callable(tool) and not getattr(tool, "__agentguard__", None):
                # ── bare callable in the list ───────────────────────────
                name = getattr(tool, "__name__", f"tool_{i}")
                wrapped = wrap_tool(self.guard, name, tool)
                tools_list[i] = wrapped
                self.guard._record_tool_registration(name, wrapped)
                log.debug("AutogenAdapter: wrapped callable _tools[%d] %r.", i, name)

    def _patch_run_json(self, idx: int, tool: Any) -> None:
        """Patch the ``run_json`` coroutine on a BaseTool-style object.

        Used when neither ``func`` nor ``_func`` is accessible (e.g. a custom
        subclass of ``BaseTool`` that doesn't store its function in a public
        or private attribute named ``func``).
        """
        tool_name: str = getattr(tool, "name", None) or f"tool_{idx}"
        original_run_json = tool.run_json
        guard = self.guard

        async def _guarded_run_json(
            args: Any,
            cancellation_token: Any,
            *pos: Any,
            **kw: Any,
        ) -> Any:
            from agentguard.models.events import EventType, Principal, RuntimeEvent, ToolCall
            from agentguard.sdk.context import current_session

            session = current_session()
            if session is not None:
                principal, goal, scope = session.principal, session.goal, session.scope
            else:
                principal = Principal(agent_id="sdk-default", session_id="anon")
                goal, scope = None, []

            raw_args: dict = args if isinstance(args, dict) else {}
            event = RuntimeEvent(
                event_type=EventType.TOOL_CALL_ATTEMPT,
                principal=principal,
                goal=goal,
                scope=list(scope),
                tool_call=ToolCall(tool_name=tool_name, args=raw_args),
            )

            # Policy check (run in thread pool to avoid blocking the event loop)
            loop = asyncio.get_running_loop()
            decision = await loop.run_in_executor(None, guard.pipeline.handle_attempt, event)

            from agentguard.models.decisions import Action
            from agentguard.models.errors import DecisionDenied, HumanApprovalPending

            mode = getattr(guard.pipeline, "mode", "enforce")
            if mode != "monitor" and mode != "dry_run":
                if decision.action is Action.DENY:
                    raise DecisionDenied(
                        reason=decision.reason or "policy_denied",
                        matched_rules=list(decision.matched_rules),
                        request_id=event.event_id,
                    )
                if decision.action is Action.HUMAN_CHECK:
                    raise HumanApprovalPending(
                        ticket_id="pending_review",
                        reason=decision.reason or "human_check_required",
                    )

            return await original_run_json(args, cancellation_token, *pos, **kw)

        try:
            object.__setattr__(tool, "run_json", _guarded_run_json)
        except (AttributeError, TypeError):
            tool.run_json = _guarded_run_json

        try:
            object.__setattr__(tool, "__agentguard_patched__", True)
        except Exception:
            pass

        self.guard._record_tool_registration(tool_name, _guarded_run_json)
        log.debug("AutogenAdapter: patched run_json on _tools[%d] %r.", idx, tool_name)

    # ── AutoGen ≤ 0.2 path ─────────────────────────────────────────────

    def _patch_function_map(self, registry: dict[str, Any]) -> None:
        for name, fn in list(registry.items()):
            if not callable(fn) or getattr(fn, "__agentguard__", None):
                continue
            registry[name] = wrap_tool(self.guard, name, fn)
            self.guard._record_tool_registration(name, registry[name])
            log.debug("AutogenAdapter: wrapped function_map[%r].", name)

    def _patch_register_function(self, obj: Any) -> None:
        original = obj.register_function

        def patched(func: Any = None, /, **kwargs: Any) -> Any:
            if callable(func) and not getattr(func, "__agentguard__", None):
                name = kwargs.get("name") or getattr(func, "__name__", "anon")
                wrapped = wrap_tool(self.guard, name, func)
                self.guard._record_tool_registration(name, wrapped)
                return original(wrapped, **kwargs)
            return original(func, **kwargs)

        obj.register_function = patched
        log.debug("AutogenAdapter: patched register_function hook.")
