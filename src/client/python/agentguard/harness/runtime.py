"""HarnessRuntime: orchestrates the full client-side execution flow."""
from __future__ import annotations

from typing import Any, Callable

from agentguard.audit.recorder import AuditRecorder
from agentguard.harness.event_bus import EventBus
from agentguard.harness.lifecycle import Lifecycle
from agentguard.harness.session import Session
from agentguard.interceptors import (
    LLMInterceptor,
    ToolInterceptor,
    ToolResultInterceptor,
)
from agentguard.sandbox.executor import SandboxExecutor
from agentguard.schemas import events as ev
from agentguard.schemas.context import RuntimeContext
from agentguard.schemas.decisions import DecisionType, GuardDecision
from agentguard.schemas.events import EventType, RuntimeEvent
from agentguard.tools.degrade import ToolDegradeManager
from agentguard.tools.metadata import ToolMetadata
from agentguard.tools.registry import ToolRegistry
from agentguard.u_guard.enforcer import EnforcementResult, UGuardEnforcer

_INTERCEPTORS = {
    EventType.LLM_INPUT: LLMInterceptor(),
    EventType.LLM_OUTPUT: LLMInterceptor(),
    EventType.TOOL_INVOKE: ToolInterceptor(),
    EventType.TOOL_RESULT: ToolResultInterceptor(),
}

_HOOK_BY_TYPE = {
    EventType.LLM_INPUT: "on_llm_input",
    EventType.LLM_OUTPUT: "on_llm_output",
    EventType.TOOL_INVOKE: "on_tool_invoke",
    EventType.TOOL_RESULT: "on_tool_result",
}


class HarnessRuntime:
    def __init__(
        self,
        *,
        context: RuntimeContext,
        enforcer: UGuardEnforcer,
        sandbox: SandboxExecutor,
        audit: AuditRecorder,
        registry: ToolRegistry | None = None,
        degrade_manager: ToolDegradeManager | None = None,
        lifecycle: Lifecycle | None = None,
        event_bus: EventBus | None = None,
        max_steps: int = 12,
        max_tool_calls: int = 24,
        window_size: int = 8,
    ) -> None:
        self.context = context
        self.enforcer = enforcer
        self.sandbox = sandbox
        self.audit = audit
        self.registry = registry or ToolRegistry()
        self.degrade = degrade_manager or ToolDegradeManager()
        self.lifecycle = lifecycle or Lifecycle()
        self.bus = event_bus or EventBus()
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.window_size = window_size
        self.session = Session(context=context)
        # Share the session trace with the audit recorder for one history.
        self.audit.trace = self.session.trace
        self.enforcer.trace_window_provider = lambda: self.session.trace.window(window_size)

    # ---- event plumbing ------------------------------------------------
    def _intercept(self, event: RuntimeEvent, phase: str) -> RuntimeEvent:
        interceptor = _INTERCEPTORS.get(event.event_type)
        if interceptor is None:
            return event
        return interceptor.before(event, self.context) if phase == "before" else interceptor.after(
            event, self.context
        )

    def guard(
        self, event: RuntimeEvent, *, force_remote: bool = False, phase: str = "before"
    ) -> EnforcementResult:
        """Run interceptors, lifecycle hooks, enforcement, and audit for an event."""
        event = self._intercept(event, phase)
        self.lifecycle.dispatch("on_event", event, self.context)
        hook = _HOOK_BY_TYPE.get(event.event_type)
        if hook:
            self.lifecycle.dispatch(hook, event, self.context)

        result = self.enforcer.enforce(event, self.context, force_remote=force_remote)
        self.audit.record(event, result.decision)
        self.bus.publish(event)
        return result

    # ---- tool flow -----------------------------------------------------
    def invoke_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        fn: Callable[..., Any],
        metadata: ToolMetadata | None = None,
    ) -> Any:
        try:
            return self._invoke_tool_inner(
                tool_name=tool_name,
                arguments=arguments,
                fn=fn,
                metadata=metadata,
            )
        except Exception:
            self.sync_local_cache_now(reason="client_error")
            raise
        finally:
            self.sync_local_cache_async(reason="round_complete")

    def _invoke_tool_inner(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        fn: Callable[..., Any],
        metadata: ToolMetadata | None = None,
    ) -> Any:
        meta = metadata or self.registry.metadata(tool_name) or ToolMetadata(name=tool_name)
        if self.session.tool_call_count >= self.max_tool_calls:
            return self._safe_error("tool call budget exceeded", tool_name)
        self.session.inc_tool_call()

        invoke_event = ev.tool_invoke(
            self.context, tool_name, arguments, capabilities=list(meta.capabilities)
        )
        result = self.guard(invoke_event)
        decision = result.decision

        if decision.decision_type == DecisionType.DENY:
            return self._safe_error(decision.reason, tool_name, decision)
        if decision.requires_user or decision.requires_remote:
            return self._pending(decision.reason, tool_name, decision)
        if decision.decision_type == DecisionType.DEGRADE:
            return self._run_degraded(tool_name, arguments, decision)

        return self._execute(tool_name, arguments, fn, list(meta.capabilities), decision)

    # ---- client/server trace sync -------------------------------------
    def sync_local_cache_async(self, *, reason: str = "round_complete") -> bool:
        remote = getattr(self.enforcer, "remote", None)
        buffer = getattr(self.enforcer, "sync_buffer", None)
        if not remote or not getattr(remote, "enabled", False) or not buffer or not buffer.has_entries():
            return False
        entries = buffer.snapshot()
        if not entries:
            return False
        trace = buffer.build_trace_upload(
            context=self.context,
            entries=entries,
            reason=reason,
        )
        remote.upload_trace_async(
            trace,
            on_success=lambda: buffer.remove_entries(entries),
        )
        return True

    def sync_local_cache_now(self, *, reason: str = "client_error") -> bool:
        remote = getattr(self.enforcer, "remote", None)
        buffer = getattr(self.enforcer, "sync_buffer", None)
        if not remote or not getattr(remote, "enabled", False) or not buffer or not buffer.has_entries():
            return False
        entries = buffer.pop_all()
        if not entries:
            return False
        trace = buffer.build_trace_upload(
            context=self.context,
            entries=entries,
            reason=reason,
        )
        try:
            remote.upload_trace(trace)
            return True
        except Exception:
            buffer.restore_front(entries)
            return False

    def _execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        fn: Callable[..., Any],
        capabilities: list[str],
        invoke_decision: GuardDecision,
    ) -> Any:
        sb = self.sandbox.run(fn, arguments, capabilities=capabilities, tool_name=tool_name)
        if not sb.success:
            err_event = ev.tool_result(self.context, tool_name, None, error=sb.error)
            self.guard(err_event, phase="after")
            return self._safe_error(sb.error or "tool failed", tool_name)

        result_event = ev.tool_result(self.context, tool_name, sb.value)
        res = self.guard(result_event, phase="after")
        rd = res.decision
        if rd.decision_type == DecisionType.DENY:
            return self._safe_error(rd.reason, tool_name, rd)
        if rd.decision_type == DecisionType.SANITIZE:
            return {"agentguard": "sanitized", "reason": rd.reason, "tool": tool_name}
        if rd.requires_user or rd.requires_remote:
            return self._pending(rd.reason, tool_name, rd)
        return sb.value

    def _run_degraded(
        self, tool_name: str, arguments: dict[str, Any], decision: GuardDecision
    ) -> Any:
        plan = self.degrade.plan(tool_name, arguments, decision.reason)
        if not plan.degraded or not plan.target_tool:
            return self._safe_error(plan.safe_error or "degradation failed", tool_name, decision)
        target = self.registry.get(plan.target_tool)
        if target is None:
            return {
                "agentguard": "degraded",
                "tool": tool_name,
                "degraded_to": plan.target_tool,
                "explanation": plan.explanation,
            }
        sb = self.sandbox.run(
            target.fn, plan.arguments, capabilities=list(target.metadata.capabilities),
            tool_name=plan.target_tool,
        )
        return sb.value if sb.success else self._safe_error(sb.error or "degraded tool failed", tool_name)

    # ---- safe results --------------------------------------------------
    @staticmethod
    def _safe_error(reason: str, tool: str, decision: GuardDecision | None = None) -> dict[str, Any]:
        return {
            "agentguard": "blocked",
            "tool": tool,
            "reason": reason,
            "decision": decision.decision_type.value if decision else "deny",
        }

    @staticmethod
    def _pending(reason: str, tool: str, decision: GuardDecision) -> dict[str, Any]:
        return {
            "agentguard": "pending",
            "tool": tool,
            "reason": reason,
            "decision": decision.decision_type.value,
        }
