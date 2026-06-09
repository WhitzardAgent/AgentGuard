"""HarnessRuntime: orchestrates the full client-side execution flow."""
from __future__ import annotations

from typing import Any, Callable

from agentguard.audit.recorder import AuditRecorder
from agentguard.harness.event_bus import EventBus
from agentguard.harness.lifecycle import Lifecycle
from agentguard.harness.session import Session
from agentguard.interceptors import (
    InputInterceptor,
    LLMInterceptor,
    MemoryInterceptor,
    OutputInterceptor,
    ThoughtInterceptor,
    ToolInterceptor,
    ToolResultInterceptor,
)
from agentguard.parser.output_router import OutputKind, route_output
from agentguard.parser.repair import repair_tool_call
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
    EventType.USER_INPUT: InputInterceptor(),
    EventType.LLM_INPUT: LLMInterceptor(),
    EventType.LLM_OUTPUT: LLMInterceptor(),
    EventType.LLM_THOUGHT: ThoughtInterceptor(),
    EventType.TOOL_INVOKE: ToolInterceptor(),
    EventType.TOOL_RESULT: ToolResultInterceptor(),
    EventType.FINAL_RESPONSE: OutputInterceptor(),
    EventType.MEMORY_READ: MemoryInterceptor(),
    EventType.MEMORY_WRITE: MemoryInterceptor(),
}

_HOOK_BY_TYPE = {
    EventType.LLM_INPUT: "on_llm_input",
    EventType.LLM_OUTPUT: "on_llm_output",
    EventType.LLM_THOUGHT: "on_llm_thought",
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
        """Run interceptors, plugin hooks, enforcement and audit for an event."""
        event = self._intercept(event, phase)
        self.lifecycle.dispatch("on_event", event, self.context)
        hook = _HOOK_BY_TYPE.get(event.event_type)
        if hook:
            self.lifecycle.dispatch(hook, event, self.context)

        ext = self._collect_extensions(event)
        result = self.enforcer.enforce(
            event, self.context, plugin_extensions=ext, force_remote=force_remote
        )
        if result.route == "remote":
            self.lifecycle.dispatch(
                "on_after_remote_decision", result.decision, self.context
            )
        plugin_results = result.decision.metadata.get("plugin_results") or {}
        self.audit.record(event, result.decision, plugin_results)
        self.bus.publish(event)
        return result

    def _collect_extensions(self, event: RuntimeEvent) -> dict[str, Any]:
        request = {
            "plugin_extensions": {},
            "trajectory_window": [e.to_dict() for e in self.session.trace.window(self.window_size)],
            "event": event.to_dict(),
        }
        out = self.lifecycle.dispatch("on_before_remote_decision", request, self.context)
        return (out or {}).get("plugin_extensions", {})

    # ---- tool flow -----------------------------------------------------
    def invoke_tool(
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

    # ---- llm output flow ----------------------------------------------
    def process_output(self, output: Any) -> dict[str, Any]:
        """Classify and guard a single LLM output. Returns a structured action."""
        routed = route_output(output)

        if routed.kind == OutputKind.THOUGHT_TRACE:
            event = ev.llm_thought(self.context, routed.thought or "")
            event.risk_signals.extend(routed.risk_signals)
            decision = self.guard(event).decision
            if decision.decision_type in (DecisionType.DROP_THOUGHT, DecisionType.DENY):
                return {"kind": "thought_dropped", "reason": decision.reason}
            return {"kind": "thought", "thought": routed.thought}

        if routed.kind == OutputKind.TOOL_CALL_CANDIDATE:
            return {"kind": "tool_calls", "tool_calls": routed.tool_calls}

        if routed.kind == OutputKind.MALFORMED_TOOL_CALL:
            return {"kind": "malformed", "errors": routed.errors}

        # final_response or unsafe_output
        event = ev.final_response(self.context, routed.text or "")
        event.risk_signals.extend(routed.risk_signals)
        decision = self.guard(event).decision
        if decision.decision_type == DecisionType.DENY:
            return {"kind": "final", "text": f"[AgentGuard blocked: {decision.reason}]", "blocked": True}
        if decision.decision_type == DecisionType.SANITIZE:
            return {"kind": "final", "text": "[AgentGuard sanitized output]", "sanitized": True}
        return {"kind": "final", "text": routed.text}

    def run_agent(self, adapter: Any, agent: Any, input_data: Any) -> dict[str, Any]:
        """Drive a guarded ReAct loop using an agent adapter."""
        ui = ev.user_input(self.context, str(input_data))
        self.guard(ui)
        messages: list[dict[str, Any]] = [{"role": "user", "content": str(input_data)}]
        last_final: str | None = None

        for _ in range(self.max_steps):
            self.session.inc_step()
            self.guard(ev.llm_input(self.context, list(messages)))
            output = adapter.generate(agent, messages, self.context)
            self.guard(ev.llm_output(self.context, output))
            action = self.process_output(output)

            if action["kind"] == "tool_calls":
                for tc in action["tool_calls"]:
                    obs = self._invoke_parsed(tc)
                    messages.append({"role": "tool", "name": tc.tool_name, "content": str(obs)})
                continue
            if action["kind"] in ("thought", "thought_dropped"):
                messages.append({"role": "assistant", "content": str(action.get("thought", ""))})
                continue
            if action["kind"] == "malformed":
                messages.append({"role": "user", "content": "Your tool call was malformed; retry."})
                continue
            last_final = action.get("text")
            break

        return {"final": last_final, "steps": self.session.step_count, "trace": self.session.trace}

    def _invoke_parsed(self, tool_call: Any) -> Any:
        reg = self.registry.get(tool_call.tool_name)
        if reg is None:
            repaired = repair_tool_call(tool_call, known_tools=self.registry.names())
            if not repaired.success or repaired.tool_call is None:
                return self._safe_error(f"unknown tool '{tool_call.tool_name}'", tool_call.tool_name)
            reg = self.registry.get(repaired.tool_call.tool_name)
            tool_call = repaired.tool_call
        if reg is None:
            return self._safe_error("tool not registered", tool_call.tool_name)
        return self.invoke_tool(
            tool_name=tool_call.tool_name,
            arguments=tool_call.arguments,
            fn=reg.fn,
            metadata=reg.metadata,
        )

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
