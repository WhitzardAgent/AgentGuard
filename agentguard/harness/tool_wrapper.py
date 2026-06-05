"""Wraps a plain tool callable so every invocation flows through the PEP.

Flow per call:
    bind args → TOOL_CALL event → middleware+PEP enforce → act on decision
    → sandboxed execution → TOOL_OBSERVATION event (re-checked for injection)
    → audit + return.
"""

from __future__ import annotations

import inspect
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable

from agentguard.harness.runtime_context import current_context
from agentguard.harness.sandbox import SandboxViolation
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decision import DecisionAction
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.tools.registry import RegisteredTool

if TYPE_CHECKING:  # avoid import cycle with the facade
    from agentguard.facade import AgentGuard


class ToolDenied(RuntimeError):
    """Raised when a tool call is denied or fails to obtain approval."""

    def __init__(self, tool_name: str, reason: str, matched_rules: list[str] | None = None) -> None:
        self.tool_name = tool_name
        self.reason = reason
        self.matched_rules = matched_rules or []
        super().__init__(f"tool '{tool_name}' denied: {reason}")


class ToolWrapper:
    def __init__(self, guard: "AgentGuard", tool: RegisteredTool) -> None:
        self._guard = guard
        self._tool = tool
        self._sig = inspect.signature(tool.fn)
        self.metadata = tool.metadata

    @property
    def name(self) -> str:
        return self.metadata.name

    def _context(self) -> RuntimeContext:
        return current_context() or self._guard.context

    def _bind_args(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            bound = self._sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            return dict(bound.arguments)
        except TypeError:
            return dict(kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        context = self._context()
        call_args = self._bind_args(args, kwargs)

        event = RuntimeEvent(
            type=EventType.TOOL_CALL,
            session_id=context.session_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            tool_name=self.name,
            args=call_args,
            capabilities=self.metadata.capability_values(),
            sink_type=self.metadata.sink_type,
        )
        self._guard._dispatch_before(event)

        result = self._guard._enforcer.enforce(event, context)
        self._guard._dispatch_after(result)

        decision = result.decision
        if decision.action is DecisionAction.DENY:
            raise ToolDenied(self.name, decision.reason, decision.matched_rules)

        if decision.action in (DecisionAction.REQUIRE_APPROVAL, DecisionAction.ASK_USER):
            approved = self._guard._request_approval(result.event, decision)
            if not approved:
                raise ToolDenied(self.name, decision.reason or "approval_denied",
                                 decision.matched_rules)

        exec_args = dict(result.event.args)
        try:
            output = self._guard._sandbox.run(
                self._tool.fn,
                args=exec_args,
                capabilities=self.metadata.capability_values(),
                tool_name=self.name,
            )
        except SandboxViolation as exc:
            raise ToolDenied(self.name, str(exc), decision.matched_rules) from exc

        return self._observe_result(output, context)

    def _observe_result(self, output: Any, context: RuntimeContext) -> Any:
        observation = RuntimeEvent(
            type=EventType.TOOL_OBSERVATION,
            session_id=context.session_id,
            user_id=context.user_id,
            agent_id=context.agent_id,
            tool_name=self.name,
            content=str(output) if output is not None else None,
            payload={"raw_type": type(output).__name__},
        )
        obs_result = self._guard._enforcer.enforce(observation, context)
        self._guard._dispatch_after(obs_result)

        if obs_result.decision.action is DecisionAction.DENY:
            raise ToolDenied(
                self.name,
                f"unsafe observation: {obs_result.decision.reason}",
                obs_result.decision.matched_rules,
            )
        if obs_result.decision.action is DecisionAction.SANITIZE:
            # Return the sanitized content rather than the raw output.
            return obs_result.event.content
        return output


def build_callable(guard: "AgentGuard", tool: RegisteredTool) -> Callable[..., Any]:
    """Return a plain function that forwards to a :class:`ToolWrapper`."""
    wrapper = ToolWrapper(guard, tool)

    @wraps(tool.fn)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        return wrapper(*args, **kwargs)

    guarded.__agentguard_wrapper__ = wrapper  # type: ignore[attr-defined]
    guarded.__agentguard_tool__ = tool  # type: ignore[attr-defined]
    return guarded
